#!/usr/bin/python

# test-running driver for ispc

# TODO: windows support (mostly should be calling CL.exe rather than gcc
#   for static linking?)

from optparse import OptionParser
import multiprocessing
from ctypes import c_int
import os
import sys
import glob
import re
import signal
import random
import string
import mutex
import subprocess
import shlex
import platform

parser = OptionParser()
parser.add_option("-r", "--random-shuffle", dest="random", help="Randomly order tests",
                  default=False, action="store_true")
parser.add_option("-s", "--static-exe", dest="static_exe", 
                  help="Create and run a regular executable for each test (rather than using the LLVM JIT).",
                  default=False, action="store_true")
parser.add_option('-t', '--target', dest='target',
                  help='Set compilation target (sse2, sse2-x2, sse4, sse4-x2, avx, avx-x2)',
                  default="sse4")
parser.add_option('-a', '--arch', dest='arch',
                  help='Set architecture (x86, x86-64)',
                  default="x86-64")
parser.add_option('-o', '--no-opt', dest='no_opt', help='Disable optimization',
                  default=False, action="store_true")

(options, args) = parser.parse_args()

# if no specific test files are specified, run all of the tests in tests/
# and failing_tests/
if len(args) == 0:
    files = glob.glob("tests/*ispc") + glob.glob("failing_tests/*ispc") + \
        glob.glob("tests_errors/*ispc")
else:
    files = args

# randomly shuffle the tests if asked to do so
if (options.random):
    random.seed()
    random.shuffle(files)

# counter
total_tests = 0
finished_tests_counter = multiprocessing.Value(c_int)

# We'd like to use the Lock class from the multiprocessing package to
# serialize accesses to finished_tests_counter.  Unfortunately, the version of
# python that ships with OSX 10.5 has this bug:
# http://bugs.python.org/issue5261.  Therefore, we use the (deprecated but
# still available) mutex class.
#finished_tests_counter_lock = multiprocessing.Lock()
finished_tests_mutex = mutex.mutex()

# utility routine to print an update on the number of tests that have been
# finished.  Should be called with the mutex (or lock) held..
def update_progress(fn):
    finished_tests_counter.value = finished_tests_counter.value + 1
    progress_str = " Done %d / %d [%s]" % (finished_tests_counter.value, total_tests, fn)
    # spaces to clear out detrius from previous printing...
    for x in range(30):
        progress_str += ' '
    progress_str += '\r'
    sys.stdout.write(progress_str)
    sys.stdout.flush()
    finished_tests_mutex.unlock()

fnull = open(os.devnull, 'w')

# run the commands in cmd_list
def run_cmds(cmd_list, filename, expect_failure):
    for cmd in cmd_list:
        if expect_failure:
            failed = (subprocess.call(cmd, shell = True, stdout = fnull, stderr = fnull) != 0)
        else:
            failed = (os.system(cmd) != 0)
        if failed:
            break

    surprise = ((expect_failure and not failed) or (not expect_failure and failed))
    if surprise == True:
        print "Test %s %s                 " % \
            (filename, "unexpectedly passed" if expect_failure else "failed")
    return surprise


# pull tests to run from the given queue and run them.  Multiple copies of
# this function will be running in parallel across all of the CPU cores of
# the system.
def run_tasks_from_queue(queue):
    error_count = 0
    while True:
        filename = queue.get()
        if (filename == 'STOP'):
            sys.exit(error_count)

        # is this a test to make sure an error is issued?
        want_error = (filename.find("tests_errors") != -1)
        if want_error == True:
            ispc_cmd = "ispc --nowrap --woff %s --arch=%s --target=%s" % \
                ( filename, options.arch, options.target)
            sp = subprocess.Popen(shlex.split(ispc_cmd), stdin=None, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            output = sp.communicate()[1]
            got_error = (sp.returncode != 0)

            # figure out the error message we're expecting
            file = open(filename, 'r')
            firstline = file.readline()
            firstline = string.replace(firstline, "//", "")
            firstline = string.lstrip(firstline)
            firstline = string.rstrip(firstline)
            file.close()

            if (output.find(firstline) == -1):
                print "Didn't see expected error message \"%s\" from test %s.\nActual outout: %s" % \
                    (firstline, filename, output)
                error_count += 1
            elif got_error == False:
                print "Unexpectedly no errors issued from test %s" % filename
                error_count += 1
            continue

        # do we expect this test to fail?
        should_fail = (filename.find("failing_") != -1)

        if options.static_exe == True:
            # if the user wants us to build a static executable to run for
            # this test, we need to figure out the signature of the test
            # function that this test has.
            sig2def = { "f_v(" : 0, "f_f(" : 1, "f_fu(" : 2, "f_fi(" : 3, 
                        "f_du(" : 4, "f_duf(" : 5, "f_di(" : 6 }
            file = open(filename, 'r')
            match = -1
            for line in file:
                # look for lines with 'export'...
                if line.find("export") == -1:
                    continue
                # one of them should have a function with one of the
                # declarations in sig2def
                for pattern, ident in sig2def.items():
                    if line.find(pattern) != -1:
                        match = ident
                        break
            file.close()
            if match == -1:
                print "Fatal error: unable to find function signature in test %s" % filename
                error_count += 1
            else:
                obj_name = "%s.o" % filename
                exe_name = "%s.run" % filename
                ispc_cmd = "ispc --woff %s -o %s --arch=%s --target=%s" % \
                    (filename, obj_name, options.arch, options.target)
                if options.no_opt:
                    ispc_cmd += " -O0" 
                if options.arch == 'x86':
                    gcc_arch = '-m32'
                else:
                    gcc_arch = '-m64'
                gcc_cmd = "g++ %s test_static.cpp -DTEST_SIG=%d %s.o -o %s" % \
                    (gcc_arch, match, filename, exe_name)
                if platform.system() == 'Darwin':
                    gcc_cmd += ' -Wl,-no_pie'
                if should_fail:
                    gcc_cmd += " -DEXPECT_FAILURE"
                    
                # compile the ispc code, make the executable, and run it...
                error_count += run_cmds([ispc_cmd, gcc_cmd, exe_name], filename, should_fail)

                # clean up after running the test
                try:
                    os.unlink(exe_name)
                    os.unlink(obj_name)
                except:
                    None
        else:
            # otherwise we'll use ispc_test + the LLVM JIT to run the test
            bitcode_file = "%s.bc" % filename
            compile_cmd = "ispc --woff --emit-llvm %s --target=%s -o %s" % \
                (filename, options.target, bitcode_file)
            if options.no_opt:
                compile_cmd += " -O0"
            test_cmd = "ispc_test %s" % bitcode_file

            error_count += run_cmds([compile_cmd, test_cmd], filename, should_fail)

            try:
                os.unlink(bitcode_file)
            except:
                None

        # If not for http://bugs.python.org/issue5261 on OSX, we'd like to do this:
        #with finished_tests_counter_lock:
            #update_progress(filename)
        # but instead we do this...
        finished_tests_mutex.lock(update_progress, filename)


task_threads = []

def sigint(signum, frame):
    for t in task_threads:
        t.terminate()
    sys.exit(1)

if __name__ == '__main__':
    nthreads = multiprocessing.cpu_count()
    total_tests = len(files)
    print "Found %d CPUs. Running %d tests." % (nthreads, total_tests)

    # put each of the test filenames into a queue
    q = multiprocessing.Queue()
    for fn in files:
        q.put(fn)
    for x in range(nthreads):
        q.put('STOP')

    # need to catch sigint so that we can terminate all of the tasks if
    # we're interrupted
    signal.signal(signal.SIGINT, sigint)

    # launch jobs to run tests
    for x in range(nthreads):
        t = multiprocessing.Process(target=run_tasks_from_queue, args=(q,))
        task_threads.append(t)
        t.start()

    # wait for them to all finish and then return the number that failed
    # (i.e. return 0 if all is ok)
    error_count = 0
    for t in task_threads:
        t.join()
        error_count += t.exitcode
    print
    if error_count > 0:
        print "%d / %d tests FAILED!" % (error_count, total_tests)
    sys.exit(error_count)
