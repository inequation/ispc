
export uniform int width() { return programCount; }



struct Foo {
    uniform double udx[5][35];
};

export void f_fu(uniform float RET[], uniform float aFOO[], uniform float b) {
    float a = aFOO[programIndex]; 
    uniform Foo f[5];
    cfor (uniform int i = 0; i < 5; ++i)
        cfor (uniform int j = 0; j < 5; ++j)
            cfor (uniform int k = 0; k < 35; ++k)
                f[i].udx[j][k] = 100*i+10*j+k;

    int x = 1;
    RET[programIndex] = f[x+1].udx[b-4][programIndex];
}

export void result(uniform float RET[]) { RET[programIndex] = 210+programIndex; }
