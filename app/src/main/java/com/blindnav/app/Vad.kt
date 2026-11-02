package com.blindnav.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.util.Log

/**
 * Silero VAD：只判断"这段有没有人在说话"，用来挡掉环境噪音。
 * 模型是 512 采样点一帧（16k 下 32ms），带 128 维状态。
 */
class Vad(context: Context) {

    companion object { private const val TAG = "BlindNavVAD" }

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession = run {
        val bytes = context.assets.open("asr/silero_vad.onnx").readBytes()
        env.createSession(bytes, OrtSession.SessionOptions())
    }
    private val state = Array(2) { Array(1) { FloatArray(128) } }
    private var stateTensor: OnnxTensor? = null
    private val srTensor: OnnxTensor = OnnxTensor.createTensor(env, longArrayOf(16000))
    private val chunk = FloatArray(512)

    fun reset() {
        for (a in state) for (b in a) b.fill(0f)
    }

    /** 一帧 512 点，返回"有人在说话"的概率 0..1。 */
    fun prob(samples: FloatArray): Float {
        return try {
            val input = OnnxTensor.createTensor(env, arrayOf(chunk).also {
                for (i in 0 until 512) chunk[i] = if (i < samples.size) samples[i] else 0f
            })
            stateTensor?.close()
            stateTensor = OnnxTensor.createTensor(env, state)
            val out = session.run(mapOf(
                "input" to input, "state" to stateTensor!!, "sr" to srTensor
            ))
            @Suppress("UNCHECKED_CAST")
            val prob = (out.get("output").get() as OnnxTensor)
                .floatBuffer.get(0)
            @Suppress("UNCHECKED_CAST")
            val next = (out.get("stateN").get() as OnnxTensor)
                .floatBuffer
            val n = next.remaining()
            next.rewind()
            val flat = FloatArray(n)
            next.get(flat)
            for (i in 0 until minOf(n, 256)) {
                state[i / 128][0][i % 128] = flat[i]
            }
            input.close()
            out.close()
            prob
        } catch (e: Throwable) {
            if (!warned) { warned = true; Log.e(TAG, "vad failed", e) }
            0f
        }
    }

    /** A failed inference is unknown, not evidence of silence. */
    fun isHealthy(): Boolean = !warned

    @Volatile private var warned = false
}
