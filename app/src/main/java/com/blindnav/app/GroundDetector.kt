package com.blindnav.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 地面设施及通道障碍候选（台阶/斑马线/门/树/垃圾）。
 * 模型来自助盲 15 类数据集微调的 YOLOv8n，只挑对走动最关键的几类说话。
 */
class GroundDetector(context: Context) {

    data class Hit(val name: String, val side: String, val near: Boolean, val score: Float)

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    private val size = 640
    private val inputBuffer = ByteBuffer.allocateDirect(3 * size * size * 4)
        .order(ByteOrder.nativeOrder()).asFloatBuffer()
    private val inputTensor: OnnxTensor

    // 必须和训练时 classes.txt 的顺序一致
    private val names = arrayOf(
        "人", "汽车", "自行车", "摩托车", "路牌", "斑马线", "绿灯", "红灯",
        "长椅", "台阶", "树", "垃圾桶", "门", "公交车", "卡车"
    )
    private val stairs = 9
    private val crosswalk = 5
    private val door = 12
    private val tree = 10
    private val trash = 11
    private val interest = setOf(stairs, crosswalk, door, tree, trash)

    init {
        val bytes = context.assets.open("belt.onnx").readBytes()
        val opts = OrtSession.SessionOptions()
        opts.setIntraOpNumThreads(2)
        session = env.createSession(bytes, opts)
        inputTensor = OnnxTensor.createTensor(
            env, inputBuffer, longArrayOf(1, 3, size.toLong(), size.toLong())
        )
    }

    fun detect(src: Bitmap): Hit? {
        val newW: Int; val newH: Int
        if (src.width >= src.height) { newW = size; newH = src.height * size / src.width }
        else { newH = size; newW = src.width * size / src.height }
        val padX = (size - newW) / 2
        val padY = (size - newH) / 2
        val scaled = Bitmap.createScaledBitmap(src, newW, newH, true)
        val square = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(square)
        canvas.drawColor(android.graphics.Color.rgb(114, 114, 114))
        canvas.drawBitmap(scaled, padX.toFloat(), padY.toFloat(), null)

        val px = IntArray(size * size)
        square.getPixels(px, 0, size, 0, 0, size, size)
        val plane = size * size
        inputBuffer.rewind()
        for (i in px.indices) {
            val p = px[i]
            inputBuffer.put(i, ((p shr 16 and 255) / 255f))
            inputBuffer.put(plane + i, ((p shr 8 and 255) / 255f))
            inputBuffer.put(2 * plane + i, ((p and 255) / 255f))
        }
        inputBuffer.rewind()

        val out = session.run(mapOf("images" to inputTensor))
        val o = (out.get("output0").get() as OnnxTensor).floatBuffer
        out.close()

        val candidates = ArrayList<GroundHazardPolicy.Candidate>()
        val n = 8400
        for (i in 0 until n) {
            for (cls in interest) {
                val score = o.get((4 + cls) * n + i)
                // 门最容易误检，门槛抬高一点；台阶宁可多报也不能漏
                val need = when (cls) { door -> 0.50f; crosswalk -> 0.45f; tree, trash -> 0.65f; else -> 0.55f }
                if (!score.isFinite() || score < need) continue
                val cx = o.get(i); val cy = o.get(n + i)
                val w = o.get(2 * n + i); val h = o.get(3 * n + i)
                if (!cx.isFinite() || !cy.isFinite() || !w.isFinite() || !h.isFinite() || w <= 0 || h <= 0) continue
                val x1 = cx - w / 2; val x2 = cx + w / 2
                val bottom = cy + h / 2
                // 只看自己正前方走廊，不看路边
                val inCorridor = x2 > padX + newW * 0.25f && x1 < padX + newW * 0.75f
                val nearEnough = bottom > padY + newH * 0.5f
                if (!inCorridor || !nearEnough) continue
                if (cls == tree || cls == trash) {
                    val pathL = padX + newW * 0.30f
                    val pathR = padX + newW * 0.70f
                    val overlap = maxOf(0f, minOf(x2, pathR) - maxOf(x1, pathL))
                    val visibleW = maxOf(0f, minOf(x2, (padX + newW).toFloat()) - maxOf(x1, padX.toFloat()))
                    val visibleH = maxOf(0f, minOf(bottom, (padY + newH).toFloat()) - maxOf(cy - h / 2, padY.toFloat()))
                    // 路边树冠/垃圾擦到边缘不触发；只作为画面中通道占用的候选，不能证明真实距离。
                    if (cx !in pathL..pathR || overlap / w < 0.35f || visibleW * visibleH < newW * newH * 0.004f) continue
                }
                val rel = (cx - (padX + newW / 2f)) / newW
                val side = when {
                    rel < -0.12f -> "左"
                    rel > 0.12f -> "右"
                    else -> "正前"
                }
                candidates.add(GroundHazardPolicy.Candidate(names[cls], side, bottom > padY + newH * 0.8f, score))
            }
        }
        return GroundHazardPolicy.choose(candidates)?.let { Hit(it.name, it.side, it.near, it.score) }
    }

    fun close() = session.close()
}
