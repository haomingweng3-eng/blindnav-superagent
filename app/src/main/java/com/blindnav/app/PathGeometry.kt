package com.blindnav.app

import kotlin.math.abs
import kotlin.math.sqrt

/** 从图像中心线判断弯曲：直线路径在透视图里可以倾斜，倾斜本身不代表拐弯。 */
object PathGeometry {
    // 输入：原图归一化的 (行位置, 横向中心偏移)，偏移正方向为右。
    fun bend(centers: List<Pair<Double, Double>>): Int {
        val valid = centers.filter { it.first.isFinite() && it.second.isFinite() }
        val near = valid.filter { it.first in 0.72..0.96 }
        val far = valid.filter { it.first in 0.45..0.65 }
        if (near.size < 10 || far.size < 10) return 0
        if (near.maxOf { it.first } - near.minOf { it.first } < 0.15 ||
            far.maxOf { it.first } - far.minOf { it.first } < 0.12) return 0
        val meanY = near.map { it.first }.average()
        val meanX = near.map { it.second }.average()
        val varianceY = near.sumOf { (it.first - meanY) * (it.first - meanY) }
        if (varianceY < 1e-6) return 0
        val slope = near.sumOf { (it.first - meanY) * (it.second - meanX) } / varianceY
        val intercept = meanX - slope * meanY
        val nearRms = sqrt(near.map { it.second - (slope * it.first + intercept) }
            .map { it * it }.average())
        if (nearRms > 0.04) return 0
        val residuals = far.map { it.second - (slope * it.first + intercept) }
        val average = residuals.average()
        val spread = sqrt(residuals.map { (it - average) * (it - average) }.average())
        val sorted = residuals.sorted()
        val median = (sorted[(sorted.size - 1) / 2] + sorted[sorted.size / 2]) / 2
        // 覆盖不足、碎片掩码或不一致的远端形状都不发出转弯方向。
        if (spread > 0.08 || abs(median) < 0.18) return 0
        return if (median > 0) 1 else -1
    }
}
