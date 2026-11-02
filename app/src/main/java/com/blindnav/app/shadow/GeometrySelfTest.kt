package com.blindnav.app.shadow

import com.google.ar.core.Pose
import kotlin.math.abs

/**
 * 坐标约定的合成自检（任务书第四点要求）。
 *
 * 目的不是做完整测试框架，而是在**真机第一次跑起来时**就把符号问题暴露出来：
 * 背投影的 X/Y/Z 任何一处取反写错，WorldPath 会整体镜像或上下翻转，
 * 而这类错误在日志里看起来完全"正常"（点数量、lateral error 都有数），
 * 只有拿已知的几何关系去对才会露出来。
 *
 * 关键点：这里用的公式是 [WorldPathShadow.pixelToCamera] 本体，
 * 不是测试里另抄一份——否则测试通过而生产代码错，比没有测试更糟。
 *
 * 只在 ARCore native 已加载后调用（Session 创建成功之后），所以放在 resume 成功之后跑一次。
 */
object GeometrySelfTest {

    private const val FX = 500.0
    private const val FY = 500.0
    private const val CX = 320.0
    private const val CY = 240.0
    private const val TOL = 0.02

    fun run(): List<String> {
        val results = ArrayList<String>()
        val identity = Pose(floatArrayOf(0f, 0f, 0f), floatArrayOf(0f, 0f, 0f, 1f))

        // 1. 光轴中心 1m → 世界 (0,0,-1)：相机看向 -Z
        runCatching {
            val cam = WorldPathShadow.pixelToCamera(CX, CY, 1.0, FX, FY, CX, CY)
            val world = identity.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            results += check("axis_forward_minus_z",
                abs(world[0]) < TOL && abs(world[1]) < TOL && abs(world[2] + 1f) < TOL,
                "expect(0,0,-1) got(%.3f,%.3f,%.3f)".format(world[0], world[1], world[2]))
        }.onFailure { results += "axis_forward_minus_z=ERROR:${it.message}" }

        // 2. 主点右侧一个焦距的像素 → 世界 X 为正（右）
        runCatching {
            val cam = WorldPathShadow.pixelToCamera(CX + FX, CY, 1.0, FX, FY, CX, CY)
            val world = identity.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            results += check("pixel_right_is_positive_x", world[0] > 0.9f,
                "expect x≈+1 got %.3f".format(world[0]))
        }.onFailure { results += "pixel_right_is_positive_x=ERROR:${it.message}" }

        // 3. 主点下方一个焦距的像素 → 世界 Y 为负（下）。图像 v 向下增长与相机 Y 向上必须靠这个负号桥接。
        runCatching {
            val cam = WorldPathShadow.pixelToCamera(CX, CY + FY, 1.0, FX, FY, CX, CY)
            val world = identity.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            results += check("pixel_down_is_negative_y", world[1] < -0.9f,
                "expect y≈-1 got %.3f".format(world[1]))
        }.onFailure { results += "pixel_down_is_negative_y=ERROR:${it.message}" }

        // 4. 相机平移 (1,2,3) 后，光轴 1m 点应落在 (1,2,2)
        runCatching {
            val moved = Pose(floatArrayOf(1f, 2f, 3f), floatArrayOf(0f, 0f, 0f, 1f))
            val cam = WorldPathShadow.pixelToCamera(CX, CY, 1.0, FX, FY, CX, CY)
            val world = moved.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            results += check("translation_applied",
                abs(world[0] - 1f) < TOL && abs(world[1] - 2f) < TOL && abs(world[2] - 2f) < TOL,
                "expect(1,2,2) got(%.3f,%.3f,%.3f)".format(world[0], world[1], world[2]))
        }.onFailure { results += "translation_applied=ERROR:${it.message}" }

        // 5. 绕 Y 轴 +90°：相机前方应变为世界 -X
        runCatching {
            val s = 0.70710678f
            val yaw90 = Pose(floatArrayOf(0f, 0f, 0f), floatArrayOf(0f, s, 0f, s))
            val forward = yaw90.rotateVector(floatArrayOf(0f, 0f, -1f))
            results += check("yaw90_forward_is_minus_x",
                forward[0] < -0.9f && abs(forward[2]) < TOL,
                "expect(-1,0,0) got(%.3f,%.3f,%.3f)".format(forward[0], forward[1], forward[2]))
            val cam = WorldPathShadow.pixelToCamera(CX, CY, 1.0, FX, FY, CX, CY)
            val world = yaw90.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            results += check("yaw90_point_is_minus_x", world[0] < -0.9f,
                "expect x≈-1 got %.3f".format(world[0]))
        }.onFailure { results += "yaw90=ERROR:${it.message}" }

        // 6. 2D 约定：世界 (x, -z) 才是"横、前"，转身不会让前方变负
        runCatching {
            val s = 0.70710678f
            val yaw90 = Pose(floatArrayOf(0f, 0f, 0f), floatArrayOf(0f, s, 0f, s))
            val cam = WorldPathShadow.pixelToCamera(CX, CY, 1.0, FX, FY, CX, CY)
            val world = yaw90.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            val forward2d = -world[2].toDouble()
            val lateral2d = world[0].toDouble()
            // 相机正前方 1m：在世界 2D 里是 x=-1、y=0；但"相对相机的前方距离"必须是 +1。
            val f = yaw90.rotateVector(floatArrayOf(0f, 0f, -1f))
            val relativeForward = lateral2d * f[0].toDouble() + (-world[2].toDouble()) * f[2].toDouble()
            results += check("camera_relative_forward_positive", abs(relativeForward - 1.0) < 0.05,
                "expect 1.0 got %.3f (world2d=(%.3f,%.3f))".format(relativeForward, lateral2d, forward2d))
        }.onFailure { results += "camera_relative_forward=ERROR:${it.message}" }

        // 7. 逆变换一致性：pose.inverse()(pose(p)) == p
        runCatching {
            val s = 0.5f
            val pose = Pose(floatArrayOf(0.3f, 1.4f, -2.2f), floatArrayOf(0f, s, 0f, 0.8660254f))
            val cam = WorldPathShadow.pixelToCamera(CX + 120, CY + 80, 2.5, FX, FY, CX, CY)
            val world = pose.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
            val back = pose.inverse().transformPoint(world)
            val err = maxOf(abs(back[0] - cam[0].toFloat()), abs(back[1] - cam[1].toFloat()),
                abs(back[2] - cam[2].toFloat()))
            results += check("inverse_roundtrip", err < 1e-3f, "max err %.6f".format(err))
        }.onFailure { results += "inverse_roundtrip=ERROR:${it.message}" }

        // 8. 重投影闭环：pixelToCamera → cameraToPixel 应回到原像素
        runCatching {
            val cam = WorldPathShadow.pixelToCamera(CX + 77.0, CY - 31.0, 1.7, FX, FY, CX, CY)
            val px = WorldPathShadow.cameraToPixel(cam[0], cam[1], -cam[2], FX, FY, CX, CY)
            val err = maxOf(abs(px[0] - (CX + 77.0)), abs(px[1] - (CY - 31.0)))
            results += check("pixel_roundtrip", err < 1e-6, "err %.8f px".format(err))
        }.onFailure { results += "pixel_roundtrip=ERROR:${it.message}" }

        return results
    }

    private fun check(name: String, ok: Boolean, detail: String) =
        "$name=${if (ok) "PASS" else "FAIL"}($detail)"
}
