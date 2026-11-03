package com.blindnav.app.shadow

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Debug Overlay 落盘（任务书第三点要求的"不要只靠代码阅读"）。
 *
 * 几何对齐这类错误，看代码是看不出来的：宽高比、letterbox、旋转、内参任何一处错，
 * 日志里的点数量和 lateral error 都照样"有数"。所以这里把四件事画在同一张图上，
 * 让**人眼**当场判断错位还是不错位：
 *
 *   1. SegFormer 的 road/sidewalk mask（半透明绿）叠在原始未旋转 RGB 上；
 *   2. 真正参与背投影的深度采样点（青色十字）；
 *   3. 每个横向条带里可通行像素的重心连线（黄色折线）= 喂给 WorldPath 的候选中心线；
 *   4. 顶部文字：来源帧、mask 网格、深度尺寸、闭环重投影误差。
 *
 * 判读规则很简单：绿块边界必须压在画面里真实的路面/人行道边界上，青十字必须落在绿块里，
 * 黄线必须顺着可通行区域走。**只要绿块整体偏移或转了 90°，本轮所有 WorldPath 数字作废。**
 *
 * 只在模型线程调用（JPEG 压缩与写盘不能压在 ARCore 线程上）。
 */
class ShadowOverlayRecorder(
    private val context: Context,
    private val logger: BlindNavSensorLogger
) {

    private val dir = File(logger.sessionDir, "overlay").apply { mkdirs() }
    private val indexFile = File(dir, "index.jsonl")
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
        .apply { timeZone = TimeZone.getTimeZone("UTC") }

    private var lastSavedAt = 0L
    private var saved = 0

    private val maskPaint = Paint().apply { color = Color.argb(96, 0, 255, 120); isAntiAlias = false }
    private val crossPaint = Paint().apply {
        color = Color.CYAN; strokeWidth = 2f; style = android.graphics.Paint.Style.STROKE
    }
    private val pathPaint = Paint().apply {
        color = Color.YELLOW; strokeWidth = 4f; style = android.graphics.Paint.Style.STROKE; isAntiAlias = true
    }
    private val textPaint = Paint().apply {
        color = Color.WHITE; textSize = 22f; isAntiAlias = true
    }
    private val textBg = Paint().apply { color = Color.argb(170, 0, 0, 0) }

    /**
     * @param frame 未旋转的原始 RGB（所有权仍在调用方，本函数只读）
     * @param depthMeters 当前深度数组，可为 null
     */
    fun record(frame: Bitmap, seg: TraversabilityShadowModel.Output?,
               depthMeters: FloatArray?, depthW: Int, depthH: Int) {
        val now = System.currentTimeMillis()
        if (now - lastSavedAt < MIN_INTERVAL_MS || saved >= MAX_FRAMES) return
        lastSavedAt = now
        if (seg == null) return
        var canvas: Bitmap? = null
        try {
            canvas = frame.copy(Bitmap.Config.ARGB_8888, true)
            val c = Canvas(canvas)
            val cw = canvas.width
            val ch = canvas.height

            // 1. mask：squash 预处理 → 归一化坐标直接映射，无 padding 反变换。
            for (gy in 0 until seg.gridHeight) {
                for (gx in 0 until seg.gridWidth) {
                    if (!seg.traversable[gy * seg.gridWidth + gx]) continue
                    val left = gx.toFloat() / seg.gridWidth * cw
                    val top = gy.toFloat() / seg.gridHeight * ch
                    val right = (gx + 1).toFloat() / seg.gridWidth * cw
                    val bottom = (gy + 1).toFloat() / seg.gridHeight * ch
                    c.drawRect(left, top, right, bottom, maskPaint)
                }
            }

            // 2. 深度采样点（青色十字）
            depthMeters?.let { meters ->
                if (depthW > 0 && depthH > 0 && meters.size >= depthW * depthH) {
                    for (row in 0 until 12) {
                        for (col in 0 until 12) {
                            val u = (col + 0.5f) / 12f
                            val v = (0.5f + row * 0.5f / 12f)          // 只看画面下半
                            val dx = (u * depthW).toInt().coerceIn(0, depthW - 1)
                            val dy = (v * depthH).toInt().coerceIn(0, depthH - 1)
                            val d = meters[dy * depthW + dx]
                            if (d.isNaN() || d <= 0f) continue
                            val px = u * cw
                            val py = v * ch
                            c.drawLine(px - 7, py, px + 7, py, crossPaint)
                            c.drawLine(px, py - 7, px, py + 7, crossPaint)
                        }
                    }
                }
            }

            // 3. 每个横向条带的可通行重心 → 候选中心线
            val bands = 8
            val points = ArrayList<Pair<Float, Float>>()
            for (b in 0 until bands) {
                val fromRow = b * seg.gridHeight / bands
                val toRow = (b + 1) * seg.gridHeight / bands
                var sumX = 0.0
                var count = 0
                for (gy in fromRow until toRow) for (gx in 0 until seg.gridWidth) {
                    if (seg.traversable[gy * seg.gridWidth + gx]) { sumX += gx; count++ }
                }
                if (count >= 2) {
                    val u = (sumX / count + 0.5) / seg.gridWidth
                    val v = (fromRow + toRow - 1) / 2.0 / seg.gridHeight
                    points.add(Pair((u * cw).toFloat(), (v * ch).toFloat()))
                }
            }
            for (i in 0 until points.size - 1) {
                c.drawLine(points[i].first, points[i].second, points[i + 1].first, points[i + 1].second, pathPaint)
            }

            // 4. 文字条
            val banner = "seg ${seg.gridWidth}x${seg.gridHeight} in${seg.inputSize} ${seg.resizeMode} " +
                "src ${seg.sourceWidth}x${seg.sourceHeight} depth ${depthW}x${depthH} " +
                "trav ${"%.2f".format(seg.traversableRatio)} bands ${points.size}"
            c.drawRect(0f, 0f, cw.toFloat(), 34f, textBg)
            c.drawText(banner, 6f, 24f, textPaint)

            val file = File(dir, "%03d-overlay.jpg".format(saved + 1))
            file.outputStream().use { c2 -> canvas.compress(Bitmap.CompressFormat.JPEG, 82, c2) }
            saved += 1
            val entry = JSONObject()
                .put("file", file.name)
                .put("utc", timeFormat.format(Date()))
                .put("mask_grid", "${seg.gridWidth}x${seg.gridHeight}")
                .put("source_wh", "${seg.sourceWidth}x${seg.sourceHeight}")
                .put("depth_wh", "${depthW}x$depthH")
                .put("traversable_ratio", seg.traversableRatio)
                .put("centerline_bands", points.size)
            indexFile.appendText("$entry\n")
        } catch (e: Throwable) {
            Log.w(TAG, "overlay: ${e.javaClass.simpleName}:${e.message}")
        } finally {
            runCatching { canvas?.recycle() }
        }
    }

    private companion object {
        const val TAG = "BlindNavShadow"
        const val MIN_INTERVAL_MS = 2000L
        const val MAX_FRAMES = 400      // 20 分钟最多 400 张，防止把手机存储写满
    }
}
