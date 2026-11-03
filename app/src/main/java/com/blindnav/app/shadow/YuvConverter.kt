package com.blindnav.app.shadow

import android.graphics.Bitmap
import android.media.Image
import kotlin.math.max
import kotlin.math.min

/**
 * ARCore `Frame.acquireCameraImage()` 的 YUV_420_888 → ARGB Bitmap（任务书 4.1 的 adapter 前半段）。
 *
 * 为什么不复用 CameraX 的 `ImageProxy.toBitmap()`：CameraActivity 拿到的 `ImageProxy` 是 CameraX
 * 包装过的对象，ARCore 返回的是裸 `android.media.Image`，没有 toBitmap()。这里自己做转换，
 * 输出与现有三个 detector 期望的一致（ARGB_8888），所以
 * `ObstacleDetector.detect(Bitmap)` / `BlindPathDetector.detect(Bitmap)` / `GroundDetector.detect(Bitmap)`
 * 一行都不用改。
 *
 * **两条路径，方向不同，不能混用：**
 *  - 几何路径（SegFormer → mask → depth → 背投影）必须用**未旋转**的图：
 *    ARCore 官方 `Camera.getImageIntrinsics()` 注释原文是 "Returns the **unrotated** camera
 *    intrinsics for the CPU image"，深度图也与未旋转的 CPU image 对齐。
 *    拿旋转过的图配未旋转内参，等于整条 WorldPath 转 90°。
 *  - 语义路径（YOLO 障碍、台阶/盲道）沿用 CameraActivity 的做法，用旋转正后的图。
 *
 * 颜色矩阵：BT.601 全范围，定点系数按 AOSP `YuvToRgbConverter` 的 1<<16 标度
 * （91881≈1.402·65536，116130≈1.772·65536，22554≈0.34414·65536，46802≈0.71414·65536），
 * **右移必须是 16**。之前写成 14 会把色度增益放大 4 倍，R/B 大面积饱和到 0/255，
 * 手机侧模型输入与电脑阶段帧不可比。这一点在真机上要用"同一帧 CameraX vs ARCore 像素差"复验。
 */
object YuvConverter {

    class Result(
        val bitmap: Bitmap,
        val lumaMean: Int,
        val width: Int,
        val height: Int,
        val rowStrideY: Int,
        val rowStrideUv: Int,
        val pixelStrideUv: Int,
        val truncated: Boolean
    )

    /**
     * @param targetRotationDegrees 0 表示保持 ARCore 原始（未旋转）方向，几何路径必须传 0。
     */
    fun toBitmap(image: Image, targetRotationDegrees: Int): Result {
        val width = image.width
        val height = image.height
        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]

        val yBuffer = yPlane.buffer
        val uBuffer = uPlane.buffer
        val vBuffer = vPlane.buffer

        val yRowStride = yPlane.rowStride
        val uvRowStride = uPlane.rowStride
        val uvPixelStride = uPlane.pixelStride
        var truncated = false

        val argb = IntArray(width * height)
        var lumaSum = 0L
        var lumaCount = 0
        val lumaStep = max(1, (width * height) / 4096)

        var out = 0
        for (row in 0 until height) {
            val yp = row * yRowStride
            val uvRow = (row / 2) * uvRowStride
            for (col in 0 until width) {
                val yIndex = yp + col
                if (yIndex >= yBuffer.limit()) { truncated = true; argb[out++] = 0xFF000000.toInt(); continue }
                val y = yBuffer.get(yIndex).toInt() and 0xFF
                val uvIndex = uvRow + col / 2 * uvPixelStride
                val u: Int; val v: Int
                if (uvIndex >= uBuffer.limit() || uvIndex >= vBuffer.limit()) {
                    truncated = true; u = 0; v = 0
                } else {
                    u = (uBuffer.get(uvIndex).toInt() and 0xFF) - 128
                    v = (vBuffer.get(uvIndex).toInt() and 0xFF) - 128
                }
                // BT.601，1<<16 标度
                var r = y + ((91881 * v) shr 16)
                var g = y - ((22554 * u + 46802 * v) shr 16)
                var b = y + ((116130 * u) shr 16)
                r = if (r < 0) 0 else if (r > 255) 255 else r
                g = if (g < 0) 0 else if (g > 255) 255 else g
                b = if (b < 0) 0 else if (b > 255) 255 else b
                argb[out] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
                if (out % lumaStep == 0) { lumaSum += y; lumaCount++ }   // 真实亮度取 Y 平面
                out++
            }
        }

        var bitmap = Bitmap.createBitmap(argb, width, height, Bitmap.Config.ARGB_8888)
        if (targetRotationDegrees != 0) {
            val m = android.graphics.Matrix().apply { postRotate(targetRotationDegrees.toFloat()) }
            val rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, m, true)
            if (rotated !== bitmap) bitmap.recycle()
            bitmap = rotated
        }
        return Result(
            bitmap = bitmap,
            lumaMean = if (lumaCount == 0) 0 else (lumaSum / lumaCount).toInt(),
            width = width, height = height,
            rowStrideY = yRowStride, rowStrideUv = uvRowStride, pixelStrideUv = uvPixelStride,
            truncated = truncated
        )
    }

    /**
     * 把 ARGB Bitmap 缩到模型输入尺寸并返回像素数组，语义与现有 detector 的 getPixels 一致。
     * 单独提供是为了让影子链路能记录"preprocess 耗时"，而不是把缩放藏在模型里测不到。
     */
    fun pixelsScaled(src: Bitmap, dstW: Int, dstH: Int): IntArray {
        val scaled = Bitmap.createScaledBitmap(src, min(dstW, src.width), min(dstH, src.height), true)
        val px = IntArray(scaled.width * scaled.height)
        scaled.getPixels(px, 0, scaled.width, 0, 0, scaled.width, scaled.height)
        if (scaled !== src) scaled.recycle()
        return px
    }

    /**
     * 语义路径（YOLO 障碍检测）用的转正旋转。
     *
     * 几何路径（SegFormer→mask→depth→背投影）**必须用未旋转帧**，因为
     * `Camera.getImageIntrinsics()` 是未旋转 CPU image 的内参；
     * 只有喂给目标检测器的图才需要和 CameraActivity 一样转正。两者分开，别混。
     */
    fun rotate(src: Bitmap, degrees: Int): Bitmap {
        if (degrees == 0) return src
        val m = android.graphics.Matrix().apply { postRotate(degrees.toFloat()) }
        return Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true)
    }
}
