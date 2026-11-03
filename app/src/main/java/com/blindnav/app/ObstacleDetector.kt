package com.blindnav.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 端侧障碍检测：COCO 版 yolov8n，只关心会挡路的那几类。
 * 判断标准是"框有没有侵入画面中间那条走廊"，不是物体类别本身。
 */
class ObstacleDetector(context: Context) {

    data class Hit(
        val name: String,
        val side: String,
        val near: Boolean,
        val score: Float,
        val widthRatio: Float = 0f,
        /** 按框底边在画面里的高度估的粗档位：近/中/远 */
        val band: String = "中",
        /** 框底边在画面里的相对位置，越大越近 */
        val bottom: Float = 0f,
        /** 与中央行走走廊的重叠占走廊宽度的比例，0..1 */
        val corridorOverlap: Float = 0f,
    )

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    private val size = 640
    private val inputBuffer = ByteBuffer.allocateDirect(3 * size * size * 4)
        .order(ByteOrder.nativeOrder()).asFloatBuffer()
    private val inputTensor: OnnxTensor

    private val names = arrayOf(
        "人", "自行车", "汽车", "摩托车", "飞机", "公交车", "火车", "卡车", "船",
        "红绿灯", "消防栓", "stop sign", "停车计时器", "长椅", "鸟", "猫", "狗", "马",
        "羊", "牛", "大象", "熊", "斑马", "长颈鹿", "背包", "雨伞", "手提包", "领带",
        "行李箱", "飞盘", "滑雪板", "单板滑雪", "运动球", "风筝", "棒球棒", "棒球手套",
        "滑板", "冲浪板", "网球拍", "瓶子", "酒杯", "杯子", "叉子", "刀", "勺子", "碗",
        "香蕉", "苹果", "三明治", "橙子", "西兰花", "胡萝卜", "热狗", "披萨", "甜甜圈",
        "蛋糕", "椅子", "沙发", "盆栽", "床", "餐桌", "马桶", "电视", "笔记本电脑",
        "鼠标", "遥控器", "键盘", "手机", "微波炉", "烤箱", "烤面包机", "水槽",
        "冰箱", "书", "时钟", "花瓶", "剪刀", "泰迪熊", "吹风机", "牙刷"
    )
    // 会挡路的类别下标
    // 行人只保留明显侵入中央通道的候选；是否播报由相机层作短时确认和场景去重。
    private val blocking = setOf(0, 1, 2, 3, 5, 7, 9, 10, 11, 13, 16, 24, 28, 56, 57, 58, 59, 60)

    init {
        val bytes = context.assets.open("yolov8n.onnx").readBytes()
        val opts = OrtSession.SessionOptions()
        opts.setIntraOpNumThreads(4)
        session = env.createSession(bytes, opts)
        inputTensor = OnnxTensor.createTensor(
            env, inputBuffer, longArrayOf(1, 3, size.toLong(), size.toLong())
        )
    }

    fun detect(bmp: Bitmap): List<Hit> {
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

        val cands = ArrayList<FloatArray>()   // x1,y1,x2,y2,score,idx
        val hitsByIdx = HashMap<Int, Hit>()
        val n = 8400
        for (i in 0 until n) {
            var cls = -1
            var score = 0.30f
            for (k in 0 until 80) {
                val s = o.get((4 + k) * n + i)
                if (s > score) { score = s; cls = k }
            }
            if (cls < 0 || cls !in blocking) continue

            val cx = o.get(i)
            val cy = o.get(n + i)
            val w = o.get(2 * n + i)
            val h = o.get(3 * n + i)
            val x1 = cx - w / 2; val x2 = cx + w / 2
            val bottom = cy + h / 2

            // 只报"真的挡在走的这条线上"的：和中间那条走了宽度的重叠要够，
            // 只是擦个边、或者离得远的，不喊
            val pathL = padX + newW * 0.30f
            val pathR = padX + newW * 0.70f
            val corridorWidth = pathR - pathL
            val overlap = maxOf(0f, minOf(x2, pathR) - maxOf(x1, pathL))
            val overlapRatio = overlap / maxOf(w, 1e-3f)
            val corridorOverlap = overlap / corridorWidth
            val relBottom = (bottom - padY) / newH
            val nearEnough = relBottom > 0.58f
            if (cls == 0) {
                val visibleHeight = maxOf(0f, minOf(bottom, (padY + newH).toFloat()) - maxOf(cy - h / 2, padY.toFloat()))
                if (!score.isFinite() || score < 0.65f || !w.isFinite() || w <= 0 || !h.isFinite() || h <= 0 ||
                    !cx.isFinite() || !cy.isFinite() || relBottom <= 0.72f || visibleHeight < newH * 0.25f) continue
                // 宽框按占用通道比例衡量，不能因覆盖整条通道反而被排除。
                if ((cx !in pathL..pathR && overlap / corridorWidth < 0.75f) ||
                    overlap / minOf(w, corridorWidth) < 0.55f) continue
            }
            // 已经很近的（快到脚前）判宽一点，别因为偏一点就漏掉
            val needOverlap = if (relBottom > 0.88f) 0.12f else 0.25f
            if (overlapRatio < needOverlap || !nearEnough) continue

            val area = w * h
            if (area < newW * newH * 0.004f) continue   // 太小的框不管
            val relative = (cx - (padX + newW / 2f)) / newW
            val side = when {
                relative < -0.12f -> "左"
                relative > 0.12f -> "右"
                else -> "正前"
            }
            val near = h > newH * 0.30f
            // 底边越靠下，障碍离脚越近（手举在胸前时的经验值，只分三档，不当米数用）
            val rel = (bottom - padY) / newH
            val band = when {
                rel > 0.82f -> "近"
                rel > 0.66f -> "中"
                else -> "远"
            }
            hitsByIdx[i] = Hit(names.getOrElse(cls) { "障碍" }, side, near, score, w / newW, band, bottom,
                corridorOverlap.coerceIn(0f, 1f))
            cands.add(floatArrayOf(x1, cy - h / 2, x2, bottom, score, i.toFloat()))
        }
        // 同一物体会有很多重叠框，先做一次 NMS，再按"离得近"排序
        cands.sortByDescending { it[4] }
        val closestPerson = hitsByIdx.entries.filter { it.value.name == "人" }
            .maxWithOrNull(compareBy<Map.Entry<Int, Hit>> { minOf(it.value.bottom, (padY + newH).toFloat()) }
                .thenBy { it.value.score })?.key
        val kept = ArrayList<FloatArray>()
        for (c in cands) {
            val isPerson = hitsByIdx[c[5].toInt()]?.name == "人"
            if (isPerson && c[5].toInt() != closestPerson) continue
            var ok = true
            for (k in kept) {
                if (isPerson != (hitsByIdx[k[5].toInt()]?.name == "人")) continue
                val ix = maxOf(0f, minOf(c[2], k[2]) - maxOf(c[0], k[0]))
                val iy = maxOf(0f, minOf(c[3], k[3]) - maxOf(c[1], k[1]))
                val inter = ix * iy
                val uni = (c[2] - c[0]) * (c[3] - c[1]) + (k[2] - k[0]) * (k[3] - k[1]) - inter
                if (uni > 0 && inter / uni > 0.45f) { ok = false; break }
            }
            if (ok) kept.add(c)
            if (kept.size >= 6) break
        }
        val outList = kept.mapNotNull { hitsByIdx[it[5].toInt()] }
        return outList.sortedWith(compareBy<Hit> { if (it.name == "人") 1 else 0 }.thenByDescending { it.bottom }).take(2)
    }

    fun close() = session.close()
}
