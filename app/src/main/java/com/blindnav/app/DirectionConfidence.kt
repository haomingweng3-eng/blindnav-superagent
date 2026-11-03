package com.blindnav.app

import kotlin.math.abs

/**
 * Cross-checks route travel bearing, phone camera heading and current visual path.
 * It never estimates body position; it only decides whether a direction cue is admissible.
 */
class DirectionConfidence {
    data class Result(val level: String, val reason: String, val canGuide: Boolean)
    private var stableSamples = 0
    private var lastScope = ""
    private var mountMode = "handheld"

    fun reset() { stableSamples = 0; lastScope = "" }

    fun setMountMode(mode: String?) {
        val next = if (mode == "chest") "chest" else "handheld"
        if (next != mountMode) { mountMode = next; reset() }
    }

    fun mountMode(): String = mountMode

    fun evaluate(
        gpsHeading: Double?, gpsAccuracy: Double?, phoneHeading: Float?,
        pathSide: String?, pathScore: Double?, speed: Double, scope: String = "",
        routeOnly: Boolean = false
    ): Result {
        if (scope != lastScope) { stableSamples = 0; lastScope = scope }
        val gpsOk = gpsHeading != null && gpsHeading.isFinite() && gpsHeading in 0.0..<360.0 &&
            gpsAccuracy != null && gpsAccuracy.isFinite() && gpsAccuracy in 0.0..20.0
        val phoneOk = phoneHeading != null && phoneHeading.isFinite() && phoneHeading in 0f..<360f
        val pathOk = pathSide == "center" && pathScore != null && pathScore.isFinite() &&
            pathScore >= if (mountMode == "chest") 0.75 else 0.65
        // A chest/neck camera is the body reference only after a stable, clear
        // visual corridor has been seen repeatedly. Handheld mode still needs
        // the phone heading because the camera can be pointed away from the body.
        if (mountMode == "chest") {
            // A chest/neck mount can use a route-only reference when the
            // path model has no usable tactile-path observation. This is
            // deliberately unavailable to handheld mode: phone heading is
            // not a body heading there. The caller must separately prove
            // that camera/obstacle/ground channels are healthy and clear.
            val routeOnlyOk = routeOnly && pathSide != "center"
            if (!gpsOk || (!pathOk && !routeOnlyOk) || speed < 0.8) {
                stableSamples = 0
                return Result("low", "胸挂画面或行进方向没有同时可靠", false)
            }
            stableSamples = (stableSamples + 1).coerceAtMost(4)
            val reason = if (routeOnlyOk) "胸挂安装和路线行进方向连续一致，未取得盲道画面" else "胸挂画面通路和行进方向连续一致"
            return if (stableSamples >= 3) Result("high", reason, true)
            else Result("medium", if (routeOnlyOk) "胸挂路线方向正在校准，请保持镜头稳定" else "胸挂方向正在校准，请保持镜头稳定并直行", false)
        }
        if (!gpsOk || !phoneOk || speed < 0.5 || !pathOk) {
            stableSamples = 0
            return Result("low", "GPS、手机航向和画面通路没有同时可靠", false)
        }
        val delta = abs(((gpsHeading!! - phoneHeading!! + 540.0) % 360.0) - 180.0)
        if (delta > 15.0) {
            stableSamples = 0
            return Result("low", "GPS和手机航向不一致", false)
        }
        stableSamples = (stableSamples + 1).coerceAtMost(3)
        return if (stableSamples >= 2) Result("high", "三种方向证据一致", true)
        else Result("medium", "方向正在校准，请先保持直行并留意脚下", false)
    }
}
