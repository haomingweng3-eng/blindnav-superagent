package com.blindnav.app

import org.json.JSONObject
import kotlin.math.abs

/** 只描述掩码在图像中的位置；没有身体朝向、脚下接触或相邻通路可通行的证据。 */
class PathObservation private constructor(
    val pathSide: String,
    val curve: String,
    val crosswalk: Boolean,
    val score: Float
) {
    val qualified: Boolean get() = pathSide != "unknown" || crosswalk

    fun text(): String {
        if (crosswalk) return "画面中疑似有斑马线，过街条件仍需现场确认"
        val position = when (pathSide) {
            "left" -> "画面下方左侧疑似有盲道，请用盲杖核对位置"
            "right" -> "画面下方右侧疑似有盲道，请用盲杖核对位置"
            "center" -> "画面下方中央疑似有盲道，脚下位置仍需用盲杖确认"
            else -> "这张画面尚不能确认盲道位置，请用盲杖核对脚下"
        }
        return position + when (curve) {
            "left" -> "。画面中盲道疑似向左弯曲，转向位置仍需现场确认"
            "right" -> "。画面中盲道疑似向右弯曲，转向位置仍需现场确认"
            else -> ""
        }
    }

    fun json(stableFrames: Int = 0): String = JSONObject().put("reference", "image").put("pathSide", pathSide)
        .put("curve", curve).put("crosswalk", crosswalk).put("score", score)
        .put("stableFrames", stableFrames)
        .put("bodyOnPath", JSONObject.NULL)
        .put("qualified", qualified).put("say", text()).toString()

    companion object {
        fun from(maskAvailable: Boolean, offset: Float?, score: Float, crosswalk: Boolean, crosswalkScore: Float, bend: Int): PathObservation {
            val path = maskAvailable && score.isFinite() && score in 0.6f..1f &&
                offset != null && offset.isFinite() && offset in -1f..1f
            val side = if (!path) "unknown" else when {
                abs(offset!!) <= 0.12f -> "center"
                offset < 0 -> "left"
                else -> "right"
            }
            val curve = if (!path) "none" else when (bend) { -1 -> "left"; 1 -> "right"; else -> "none" }
            val crossing = crosswalk && crosswalkScore.isFinite() && crosswalkScore in 0.4f..1f
            return PathObservation(side, curve, crossing, if (path) score else 0f)
        }
    }
}
