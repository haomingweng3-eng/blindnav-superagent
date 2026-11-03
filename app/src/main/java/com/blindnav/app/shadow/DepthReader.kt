package com.blindnav.app.shadow

import android.media.Image
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * ARCore 深度图读取与统计（任务书 4.3 / 第十节）。
 *
 * 位宽按 ARCore 1.57 官方源码逐条核对，**不是**旧 DEPTH16 的 13-bit：
 *  - `acquireDepthImage16Bits()` / `acquireRawDepthImage16Bits()`：单 plane、16-bit 小端、
 *    单位毫米、表示"到相机平面的距离"，**可表示范围 0..65535mm（约 65m），不做任何位掩码**。
 *    0 = 无有效深度。
 *  - 13-bit / 0..8191mm 那套说法属于**已废弃**的 `acquireDepthImage()` / `acquireRawDepthImage()`，
 *    其注释明确写着"Please use acquireDepthImage16Bits instead, which expands the depth range
 *    from 8191mm to 65535mm"，并说废弃版更慢是因为要清每个像素的高 3 位。
 *    对 16Bits API 再清高 3 位会把 ≥8.192m 全部折叠（12m 会变成 3.808m）。
 *  - `acquireRawDepthConfidenceImage()`：8-bit，0..255，0 = 该像素无有效深度；
 *    官方建议过滤阈值 128。**与 depth/raw depth 尺寸完全相同**，所以按 (row, col) 同网格索引，
 *    绝不能按 (row/2, col/2) 取（那是把左上象限另一个像素的置信度安到当前像素头上）。
 *  - 深度最佳精度 0.5m–15m，可靠观测到 25m；误差随距离平方增长。
 *  - 算力不足时 `acquire*` 可能返回**更早一帧**的深度图，因此比对 `Image.getTimestamp()`
 *    与 frame 时间戳，把 staleness 记下来而不是假装同步。
 */
class DepthReader {

    class Stats(
        val width: Int,
        val height: Int,
        val imageTimestampNanos: Long,
        val validPixels: Int,
        val confidentPixels: Int,
        val coverage: Double,
        val confidentCoverage: Double,
        val meanConfidence: Double,
        val confidenceHistogram: IntArray,
        val minMeters: Double,
        val medianMeters: Double,
        /** 画面下方中央带（最可能挡脚前的区域）的中位距离，米。 */
        val nearBandMedianMeters: Double,
        val nearBandConfidentMedianMeters: Double,
        val zeroDepthCount: Int,
        val maxMmSeen: Int,
        /** 有多少像素超过 8191mm：这一台设备到底走 13-bit 还是 16-bit 约定，由真机数据回答。 */
        val beyond8191mm: Int,
        val confidenceSizeMatches: Boolean,
        val confidenceReadSkipped: Boolean
    ) {
        fun json(frameTimestampNanos: Long) = linkedMapOf<String, Any?>(
            "depth_w" to width,
            "depth_h" to height,
            "depth_ts_ns" to imageTimestampNanos,
            "depth_staleness_ms" to ((frameTimestampNanos - imageTimestampNanos + 500_000L) / 1_000_000L),
            "depth_valid_px" to validPixels,
            "depth_confident_px" to confidentPixels,
            "depth_coverage" to "%.4f".format(coverage).toDouble(),
            "depth_confident_coverage" to "%.4f".format(confidentCoverage).toDouble(),
            "depth_mean_confidence" to "%.3f".format(meanConfidence).toDouble(),
            "depth_conf_hist" to confidenceHistogram.joinToString(","),
            "depth_min_m" to minMeters,
            "depth_median_m" to medianMeters,
            "depth_nearband_median_m" to nearBandMedianMeters,
            "depth_nearband_confident_median_m" to nearBandConfidentMedianMeters,
            "depth_zero_px" to zeroDepthCount,
            "depth_max_mm_seen" to maxMmSeen,
            "depth_px_beyond_8191mm" to beyond8191mm,
            "depth_conf_size_matches" to confidenceSizeMatches,
            "depth_conf_read_skipped" to confidenceReadSkipped
        )
    }

    /**
     * 读取 16-bit 深度图，返回米制数组（NaN = 无效）与统计。
     *
     * @param confidence 可为 null（该帧置信度图尚未就绪，或调用方只要 full depth）。
     *                   为 null 时 confident 系列指标为 0，**不当成"全部可信"**。
     */
    fun read(image: Image, confidence: Image?): Pair<FloatArray, Stats> {
        val w = image.width
        val h = image.height
        val plane = image.planes[0]
        val buffer = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val confPlane = confidence?.planes?.getOrNull(0)
        val confBuffer = confPlane?.buffer
        val confRowStride = confPlane?.rowStride ?: 0
        val confPixelStride = confPlane?.pixelStride ?: 0
        val confMatches = confidence != null && confidence.width == w && confidence.height == h
        var confUsable = confBuffer != null && confMatches && confRowStride > 0
        // 尺寸不一致时不猜映射，直接标 skipped：错位统计比没有统计更有害。
        var confSkipped = confidence != null && !confMatches

        val meters = FloatArray(w * h) { Float.NaN }
        val hist = IntArray(8)
        var valid = 0
        var confident = 0
        var confSum = 0L
        var zeroDepth = 0
        var maxMm = 0
        var beyond8191 = 0
        val sorted = ArrayList<Int>(minOf(w * h, 40_000))
        val nearValues = ArrayList<Int>()
        val nearConfidentValues = ArrayList<Int>()

        for (row in 0 until h) {
            val rowBase = row * rowStride
            val confRowBase = row * confRowStride
            val inNearBand = row >= h * NEAR_BAND_FROM
            for (col in 0 until w) {
                val index = rowBase + col * pixelStride
                if (index + 1 >= buffer.limit()) continue
                val lo = buffer.get(index).toInt() and 0xFF
                val hi = buffer.get(index + 1).toInt() and 0xFF
                // 完整 16-bit，小端，不 mask。0 = 无有效深度。
                val mm = lo or (hi shl 8)
                val out = row * w + col
                if (mm <= 0) { zeroDepth++; continue }
                valid++
                if (mm > maxMm) maxMm = mm
                if (mm > 8191) beyond8191++
                meters[out] = mm / 1000f
                if (sorted.size < 40_000) sorted.add(mm)
                var conf = -1
                if (confUsable) {
                    val ci = confRowBase + col * confPixelStride
                    if (ci in 0 until confBuffer!!.limit()) conf = confBuffer.get(ci).toInt() and 0xFF
                }
                if (conf >= 0) {
                    confSum += conf
                    hist[(conf / 32).coerceIn(0, 7)] += 1
                    if (conf >= CONFIDENCE_THRESHOLD) {
                        confident++
                        if (inNearBand) nearConfidentValues.add(mm)
                    }
                }
                if (inNearBand) nearValues.add(mm)
            }
        }
        val total = w * h
        val stats = Stats(
            width = w, height = h,
            imageTimestampNanos = runCatching { image.timestamp }.getOrDefault(0L),
            validPixels = valid,
            confidentPixels = confident,
            coverage = if (total == 0) 0.0 else valid.toDouble() / total,
            confidentCoverage = if (total == 0) 0.0 else confident.toDouble() / total,
            meanConfidence = if (hist.sum() == 0) 0.0 else confSum.toDouble() / hist.sum(),
            confidenceHistogram = hist,
            minMeters = (sorted.minOrNull() ?: 0) / 1000.0,
            medianMeters = median(sorted) / 1000.0,
            nearBandMedianMeters = median(nearValues) / 1000.0,
            nearBandConfidentMedianMeters = median(nearConfidentValues) / 1000.0,
            zeroDepthCount = zeroDepth,
            maxMmSeen = maxMm,
            beyond8191mm = beyond8191,
            confidenceSizeMatches = confMatches,
            confidenceReadSkipped = confSkipped || (confBuffer != null && !confUsable && !confMatches)
        )
        return Pair(meters, stats)
    }

    private fun median(values: List<Int>): Double {
        if (values.isEmpty()) return 0.0
        val s = values.sorted()
        return if (s.size % 2 == 1) s[s.size / 2].toDouble() else (s[s.size / 2 - 1] + s[s.size / 2]) / 2.0
    }

    /**
     * 静止时同一区域的时间抖动（任务书第十节 Stability）。
     *
     * 参照系只在"相机确实移动过"之后才重置：静止保持期间一直和**进入静止那一刻**的网格比，
     * 这样量到的是整段静止期累计的抖动/漂移；如果每帧都覆盖参照，累计漂移会被抹掉，
     * 只剩相邻帧差，数字会系统性偏小。
     *
     * 注意：手持静止时 ARCore 自身位姿也会漂，所以这个数混了"深度噪声 + SLAM 漂移"。
     * 报告时不能把它单独说成深度噪声。
     */
    class JitterTracker {
        private var reference: FloatArray? = null
        private var referencePose: DoubleArray? = null
        var jitterSamples: Long = 0L
            private set
        var jitterSum: Double = 0.0
            private set
        var jitterMax: Double = 0.0
            private set
        var staticFramePairs: Long = 0L
            private set
        var staticHolds: Long = 0L
            private set

        fun observe(meters: FloatArray, width: Int, height: Int,
                    tx: Double, ty: Double, tz: Double, qw: Double, qx: Double, qy: Double, qz: Double) {
            val pose = doubleArrayOf(tx, ty, tz, qw, qx, qy, qz)
            val ref = reference
            val refPose = referencePose
            if (ref == null || refPose == null || ref.size != meters.size || !staticBetween(refPose, pose)) {
                // 相机移动了（或第一次）：把当前帧立为新参照，开始一段新的静止窗口。
                reference = meters.copyOf()
                referencePose = pose
                if (ref != null) staticHolds += 1
                return
            }
            staticFramePairs += 1
            val cells = gridMedians(meters, width, height)
            val refCells = gridMedians(ref, width, height)
            for (i in cells.indices) {
                val a = cells[i]; val b = refCells[i]
                if (a.isNaN() || b.isNaN()) continue
                val d = abs(a - b).toDouble()
                jitterSamples += 1
                jitterSum += d
                if (d > jitterMax) jitterMax = d
            }
        }

        private fun staticBetween(a: DoubleArray, b: DoubleArray): Boolean {
            val dx = a[0] - b[0]; val dy = a[1] - b[1]; val dz = a[2] - b[2]
            val translation = sqrt(dx * dx + dy * dy + dz * dz)
            val dot = abs(a[3] * b[3] + a[4] * b[4] + a[5] * b[5] + a[6] * b[6]).coerceAtMost(1.0)
            val rotation = Math.toDegrees(2.0 * Math.acos(dot))
            return translation < STATIC_TRANSLATION_M && rotation < STATIC_ROTATION_DEG
        }

        private fun gridMedians(meters: FloatArray, width: Int, height: Int): FloatArray {
            val out = FloatArray(GRID * GRID)
            val cellW = width / GRID
            val cellH = height / GRID
            for (gy in 0 until GRID) for (gx in 0 until GRID) {
                val values = ArrayList<Float>(cellW * cellH / 4 + 1)
                for (y in gy * cellH until minOf((gy + 1) * cellH, height)) {
                    for (x in gx * cellW until minOf((gx + 1) * cellW, width)) {
                        val v = meters.getOrNull(y * width + x) ?: Float.NaN
                        if (!v.isNaN()) values.add(v)
                    }
                }
                out[gy * GRID + gx] = if (values.isEmpty()) Float.NaN else {
                    values.sort(); values[values.size / 2]
                }
            }
            return out
        }

        fun json() = linkedMapOf<String, Any?>(
            "depth_static_pairs" to staticFramePairs,
            "depth_static_holds" to staticHolds,
            "depth_jitter_samples" to jitterSamples,
            "depth_jitter_mean_m" to if (jitterSamples == 0L) null else "%.4f".format(jitterSum / jitterSamples).toDouble(),
            "depth_jitter_max_m" to if (jitterSamples == 0L) null else "%.4f".format(jitterMax).toDouble()
        )

        fun reset() { reference = null; referencePose = null }

        companion object {
            const val STATIC_TRANSLATION_M = 0.02
            const val STATIC_ROTATION_DEG = 1.0
            const val GRID = 4
        }
    }

    companion object {
        /** 官方建议的一半置信度阈值。 */
        const val CONFIDENCE_THRESHOLD = 128
        /** "近场带"统一定义：画面下方 30%。DepthReader 与 Engine 必须用同一个值，否则两个"近场中位距离"不可比。 */
        const val NEAR_BAND_FROM = 0.70f
        const val NEAR_BAND_LEFT = 0.35f
        const val NEAR_BAND_RIGHT = 0.65f
    }
}
