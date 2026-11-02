package com.blindnav.app;

import kotlin.jvm.functions.Function1;

/**
 * sherpa-onnx JNI 按 ([F)Ljava/lang/Integer; 反射查找回调方法。
 * Kotlin lambda 经 D8 脱糖后只剩 invoke(Object)Object，会触发 NoSuchMethodError。
 * 显式 Java 类由 javac 生成该签名的具体方法和桥接方法。
 */
public final class TtsCallback implements Function1<float[], Integer> {
    public interface Gate {
        boolean shouldContinue();
    }

    private final Gate gate;

    public TtsCallback(Gate gate) {
        this.gate = gate;
    }

    @Override
    public Integer invoke(float[] samples) {
        return gate.shouldContinue() ? 1 : 0;
    }
}
