package com.blindnav.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import java.nio.FloatBuffer

/**
 * 端侧盲道/斑马线分割（开源模型 AIGlasses_for_navigation）。
 * 输入 640x640 letterbox，输出 boxes+classes+mask 系数，用 mask 判断脚下是否走偏。
 */
class BlindPathDetector(context: Context) {

    data class Result(
        val onPath: Boolean,     // 图像下方可取得该掩码，不表示人的脚已在盲道上。
        val offset: Float?,      // 相对画面中心，正=右，负=左。
        val crosswalk: Boolean,
        val say: String,
        val score: Float = 0f,
        /** 前方盲道是否拐弯：0 不弯，-1 往左，+1 往右 */
        val bend: Int = 0,
        val crosswalkScore: Float = 0f
    )

    private val env: OrtEnvironment = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    private val size = 640
    private val chw = FloatArray(3 * size * size)
    private val inputBuffer = java.nio.ByteBuffer.allocateDirect(chw.size * 4)
        .order(java.nio.ByteOrder.nativeOrder()).asFloatBuffer()
    private val inputTensor: OnnxTensor

    init {
        val bytes = context.assets.open("yolo-seg-int8.onnx").readBytes()
        val opts = OrtSession.SessionOptions()
        opts.setIntraOpNumThreads(4)
        session = env.createSession(bytes, opts)
        inputTensor = OnnxTensor.createTensor(
            env, inputBuffer, longArrayOf(1, 3, size.toLong(), size.toLong())
        )
    }

    fun close() = session.close()

    fun detect(bmp: Bitmap): Result {
        val scale = minOf(size.toFloat() / bmp.width, size.toFloat() / bmp.height)
        val newW = (bmp.width * scale).toInt()
        val newH = (bmp.height * scale).toInt()
        val padX = (size - newW) / 2
        val padY = (size - newH) / 2

        val scaled = Bitmap.createScaledBitmap(bmp, newW, newH, true)
        val canvas = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val c = android.graphics.Canvas(canvas)
        c.drawColor(android.graphics.Color.rgb(114, 114, 114))
        c.drawBitmap(scaled, padX.toFloat(), padY.toFloat(), null)

        val px = IntArray(size * size)
        canvas.getPixels(px, 0, size, 0, 0, size, size)
        val plane = size * size
        for (i in px.indices) {
            val p = px[i]
            chw[i] = ((p shr 16 and 255) / 255f)
            chw[plane + i] = ((p shr 8 and 255) / 255f)
            chw[2 * plane + i] = ((p and 255) / 255f)
        }
        inputBuffer.rewind()
        inputBuffer.put(chw)
        inputBuffer.rewind()

        val out = session.run(mapOf("images" to inputTensor))
        val boxes = (out.get("output0").get() as OnnxTensor).floatBuffer
        val proto = (out.get("output1").get() as OnnxTensor).floatBuffer

        val n = 8400
        val nc = 2
        val masks = 32
        var bestBlind = -1
        var bestBlindScore = 0.25f
        var bestCross = 0f
        val cand = ArrayList<FloatArray>()   // cx, cy, w, h, score, class, index

        for (i in 0 until n) {
            val s0 = boxes.get(4 * n + i)
            val s1 = boxes.get(5 * n + i)
            val score = maxOf(s0, s1)
            if (score < 0.25f) continue
            val cls = if (s1 > s0) 1 else 0
            val cx = boxes.get(i)
            val cy = boxes.get(n + i)
            val w = boxes.get(2 * n + i)
            val h = boxes.get(3 * n + i)
            cand.add(floatArrayOf(cx, cy, w, h, score, cls.toFloat(), i.toFloat()))
            if (cls == 1 && score > bestBlindScore) { bestBlindScore = score; bestBlind = i }
            if (cls == 0) bestCross = maxOf(bestCross, score)
        }
        val picked = nms(cand, 0.45f)
        out.close()

        val crosswalk = bestCross >= 0.4f
        if (bestBlind < 0) {
            val say = PathObservation.from(false, null, 0f, crosswalk, bestCross, 0).text()
            return Result(false, null, crosswalk, say, 0f, 0, bestCross)
        }

        val offset = bandOffset(proto, boxes, bestBlind, padX, padY, newW, newH, 0.75f, 1.0f)
        val bend = PathGeometry.bend(maskCenters(proto, boxes, bestBlind, padX, padY, newW, newH))
        val say = PathObservation.from(offset != null, offset, bestBlindScore, crosswalk, bestCross, bend).text()
        return Result(offset != null, offset, crosswalk, say, bestBlindScore, bend, bestCross)
    }

    /** 某一高度带内盲道掩码的水平重心，换算成相对画面中心的偏移。 */
    private fun bandOffset(
        proto: FloatBuffer, boxes: FloatBuffer, det: Int,
        padX: Int, padY: Int, newW: Int, newH: Int, from: Float, to: Float
    ): Float? {
        val m = 160
        val coeffOff = 6 * 8400
        val bandStart = padY + (newH * from).toInt()
        val bandEnd = padY + (newH * to).toInt()
        // YOLO 分割的 prototype 覆盖整幅图；该实例的掩码只能在它自己的框内使用。
        // 否则框外纹理也会进入重心，右侧盲道甚至会被算到左边。
        val cxBox = boxes.get(det)
        val cyBox = boxes.get(8400 + det)
        val wBox = boxes.get(2 * 8400 + det)
        val hBox = boxes.get(3 * 8400 + det)
        val x0 = maxOf(0, kotlin.math.ceil(maxOf(padX.toFloat(), cxBox - wBox / 2) / 4).toInt())
        val x1 = minOf(m, kotlin.math.ceil(minOf((padX + newW).toFloat(), cxBox + wBox / 2) / 4).toInt())

        var sumX = 0.0
        var count = 0
        val y0 = maxOf(0, bandStart / 4, kotlin.math.ceil((cyBox - hBox / 2) / 4).toInt())
        val y1 = minOf(m, bandEnd / 4, kotlin.math.ceil((cyBox + hBox / 2) / 4).toInt())
        for (y in y0 until minOf(y1, m)) {
            for (x in x0 until x1) {
                var v = 0f
                for (k in 0 until 32) {
                    v += boxes.get(coeffOff + k * 8400 + det) * proto.get(k * m * m + y * m + x)
                }
                if (v > -20f && 1f / (1f + Math.exp(-v.toDouble())) > 0.5) {
                    sumX += x * 4.0
                    count++
                }
            }
        }
        if (count < 30) return null
        val cx = (sumX / count).toFloat()
        val center = padX + newW / 2f
        return (cx - center) / (newW / 2f)
    }

    private fun maskCenters(proto: FloatBuffer, boxes: FloatBuffer, det: Int,
                            padX: Int, padY: Int, newW: Int, newH: Int): List<Pair<Double, Double>> {
        val cx = boxes.get(det); val cy = boxes.get(8400 + det)
        val w = boxes.get(2 * 8400 + det); val h = boxes.get(3 * 8400 + det)
        val x0 = maxOf(0, kotlin.math.ceil(maxOf(padX.toFloat(), cx - w / 2) / 4).toInt())
        val x1 = minOf(160, kotlin.math.ceil(minOf((padX + newW).toFloat(), cx + w / 2) / 4).toInt())
        val y0 = maxOf(0, (padY + newH * 0.40f).toInt() / 4, kotlin.math.ceil((cy - h / 2) / 4).toInt())
        val y1 = minOf(160, (padY + newH * 0.96f).toInt() / 4, kotlin.math.ceil((cy + h / 2) / 4).toInt())
        val coefficients = FloatArray(32) { boxes.get((6 + it) * 8400 + det) }
        val rows = ArrayList<Pair<Double, Double>>()
        for (y in y0 until y1) {
            var sum = 0.0; var count = 0
            for (x in x0 until x1) {
                var value = 0f
                for (k in 0 until 32) value += coefficients[k] * proto.get(k * 160 * 160 + y * 160 + x)
                if (value > 0f) { sum += x * 4.0; count++ }
            }
            if (count >= 3) rows.add((y * 4.0 - padY) / newH to
                ((sum / count) - (padX + newW / 2.0)) / (newW / 2.0))
        }
        return rows
    }

    private fun nms(c: ArrayList<FloatArray>, thr: Float): List<FloatArray> {
        c.sortByDescending { it[4] }
        val keep = ArrayList<FloatArray>()
        for (a in c) {
            if (keep.none { b -> a[5] == b[5] && iou(a, b) > thr }) keep.add(a)
        }
        return keep
    }

    private fun iou(a: FloatArray, b: FloatArray): Float {
        val ax1 = a[0] - a[2] / 2; val ay1 = a[1] - a[3] / 2
        val ax2 = a[0] + a[2] / 2; val ay2 = a[1] + a[3] / 2
        val bx1 = b[0] - b[2] / 2; val by1 = b[1] - b[3] / 2
        val bx2 = b[0] + b[2] / 2; val by2 = b[1] + b[3] / 2
        val iw = maxOf(0f, minOf(ax2, bx2) - maxOf(ax1, bx1))
        val ih = maxOf(0f, minOf(ay2, by2) - maxOf(ay1, by1))
        val inter = iw * ih
        val uni = a[2] * a[3] + b[2] * b[3] - inter
        return if (uni <= 0f) 0f else inter / uni
    }
}
