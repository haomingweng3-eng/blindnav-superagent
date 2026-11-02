package com.blindnav.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Frozen core_v1 desktop evaluation.
 *
 * Legacy model reproduces the pre-change CameraActivity rule:
 *   stop = any detector hit OR near-focus evidence.
 * New model is WalkingSafetyPolicy, the production path.
 */
class CoreV1WalkingObstacleEvalTest {

    private data class ObstacleCase(
        val id: String,
        val name: String,
        val corridor: Boolean,
        val hits: List<WalkingSafetyPolicy.Hit>,
        val nearFocus: Boolean,
        val walking: Boolean,
        val singleFrame: Boolean,
        val expectedStop: Boolean,
    )

    private fun obstacleCases(): List<ObstacleCase> = listOf(
        ObstacleCase("O1", "chair blocks corridor", true,
            listOf(WalkingSafetyPolicy.Hit("椅子", "近", 0.90f, 0.90f)), false, true, false, true),
        ObstacleCase("O2", "left wall very close", false, emptyList(), true, false, false, false),
        ObstacleCase("O3", "bicycle outside corridor", true, emptyList(), false, true, false, false),
        ObstacleCase("O4", "person crosses corridor", true,
            listOf(WalkingSafetyPolicy.Hit("人", "中", 0.70f, 0.70f)), false, true, false, true),
        ObstacleCase("O5", "obstacle approaching", true,
            listOf(WalkingSafetyPolicy.Hit("障碍", "中", 0.45f, 0.70f)), false, true, false, true),
        ObstacleCase("O6", "static desk with laptop", true, emptyList(), true, false, false, false),
        ObstacleCase("O7", "door/wall close while looking", false, emptyList(), true, false, false, false),
        ObstacleCase("O8", "unknown object blocks corridor", true,
            listOf(WalkingSafetyPolicy.Hit("障碍", "近", 0.80f, 0.90f)), false, true, false, true),
        ObstacleCase("N1", "single-frame detector noise", true,
            listOf(WalkingSafetyPolicy.Hit("椅子", "近", 0.90f, 0.90f)), false, true, true, false),
    )

    private fun stopFrames(case: ObstacleCase, useNew: Boolean, frames: Int = 8): Int {
        val policy = WalkingSafetyPolicy()
        var stops = 0
        val start = 100_000L
        for (f in 0 until frames) {
            val now = start + f * 150L
            val hits = if (case.singleFrame && f > 0) emptyList() else case.hits
            if (useNew) {
                val d = policy.evaluate(WalkingSafetyPolicy.Input(
                    nowMs = now,
                    corridorAvailable = case.corridor,
                    corridorSide = "center",
                    corridorCurve = "none",
                    corridorScore = 0.9f,
                    hits = hits,
                    nearFocus = case.nearFocus,
                    nearFocusSinceMs = if (case.nearFocus) start else 0L,
                    walking = case.walking,
                    rotationSuspected = false,
                ))
                if (d.risk == "STOP") stops++
            } else {
                if (hits.isNotEmpty() || case.nearFocus) stops++
            }
        }
        return stops
    }

    private fun recoverMs(useNew: Boolean): Long {
        val policy = WalkingSafetyPolicy()
        val start = 100_000L
        var stopped = false
        for (f in 0 until 5) {
            val now = start + f * 150L
            val d = policy.evaluate(WalkingSafetyPolicy.Input(
                now, true, "center", "none", 0.9f,
                listOf(WalkingSafetyPolicy.Hit("椅子", "近", 0.9f, 0.9f)),
                false, 0L, true, false))
            if (d.risk == "STOP") stopped = true
        }
        if (!useNew) return 0L
        if (!stopped) return -1L
        for (f in 5 until 25) {
            val now = start + f * 150L
            val d = policy.evaluate(WalkingSafetyPolicy.Input(
                now, true, "center", "none", 0.9f, emptyList(),
                false, 0L, true, false))
            if (d.risk != "STOP") return (f - 5) * 150L
        }
        return -1L
    }

    private fun commands(sides: List<String>, rotation: Boolean, useNew: Boolean): List<String> {
        val policy = WalkingSafetyPolicy()
        val out = ArrayList<String>()
        for ((i, side) in sides.withIndex()) {
            val now = 100_000L + i * 150L
            if (useNew) {
                val d = policy.evaluate(WalkingSafetyPolicy.Input(
                    now, side != "unknown", side, "none", 0.9f, emptyList(),
                    false, 0L, true, rotation))
                out.add(d.pathCommand)
            } else {
                out.add(when (side) { "left" -> "LEFT"; "right" -> "RIGHT"; "center" -> "KEEP"; else -> "KEEP" })
            }
        }
        return out
    }

    private fun flips(cmds: List<String>): Int {
        var last = "KEEP"; var n = 0
        for (c in cmds) {
            if ((c == "LEFT" || c == "RIGHT") && (last == "LEFT" || last == "RIGHT") && c != last) n++
            if (c == "LEFT" || c == "RIGHT") last = c
        }
        return n
    }

    private fun firstSide(cmds: List<String>): Int = cmds.indexOfFirst { it == "LEFT" || it == "RIGHT" }

    @Test
    fun frozenObstacleAndWalkingMetrics() {
        val cases = obstacleCases()
        val baselineFalse = cases.filter { !it.expectedStop && stopFrames(it, false) > 0 }
        val newFalse = cases.filter { !it.expectedStop && stopFrames(it, true) > 0 }
        val positives = cases.filter { it.expectedStop }
        val baselineRecall = positives.count { stopFrames(it, false) > 0 }
        val newRecall = positives.count { stopFrames(it, true) > 0 }

        val noise = cases.first { it.id == "N1" }
        val noiseBaseline = stopFrames(noise, false)
        val noiseNew = stopFrames(noise, true)

        val straight = commands(List(8) { "center" }, false, true)
        val driftLeft = commands(List(8) { "left" }, false, true)
        val driftRight = commands(List(8) { "right" }, false, true)
        val rotationOnly = listOf("left", "left", "center", "center", "center", "center")
        val rotationNew = commands(rotationOnly, true, true)
        val rotationOld = commands(rotationOnly, true, false)
        val oscillation = List(40) { if ((it / 2) % 2 == 0) "left" else "right" }
        val oscNew = commands(oscillation, true, true)
        val oscOld = commands(oscillation, true, false)
        val recoveryPath = listOf("left", "left", "left", "left") + List(8) { "center" }
        val recNew = commands(recoveryPath, false, true)
        val recOld = commands(recoveryPath, false, false)
        val unavailable = commands(List(8) { "unknown" }, false, true)

        val recoveryNewMs = recoverMs(true)
        val recoveryOldMs = recoverMs(false)
        val oscMinutes = oscillation.size * 150.0 / 60_000.0
        val newFlipsPerMin = if (oscMinutes > 0) flips(oscNew) / oscMinutes else 0.0
        val oldFlipsPerMin = if (oscMinutes > 0) flips(oscOld) / oscMinutes else 0.0

        val metrics = """
        {
          "status": "DESKTOP_SIMULATION",
          "obstacle": {
            "baseline_false_stop_cases": ${baselineFalse.size},
            "new_false_stop_cases": ${newFalse.size},
            "negative_cases": ${cases.count { !it.expectedStop }},
            "baseline_recall": "$baselineRecall/${positives.size}",
            "new_recall": "$newRecall/${positives.size}",
            "single_frame_noise_stop_frames_baseline": $noiseBaseline,
            "single_frame_noise_stop_frames_new": $noiseNew,
            "recovery_ms_baseline": $recoveryOldMs,
            "recovery_ms_new": $recoveryNewMs
          },
          "walking": {
            "straight_commands": [${straight.joinToString(",") { "\"$it\"" }}],
            "true_left_detection_frame_baseline": ${firstSide(commands(List(8) { "left" }, false, false))},
            "true_left_detection_frame_new": ${firstSide(driftLeft)},
            "true_right_detection_frame_baseline": ${firstSide(commands(List(8) { "right" }, false, false))},
            "true_right_detection_frame_new": ${firstSide(driftRight)},
            "rotation_only_side_frames_baseline": ${rotationOld.count { it == "LEFT" || it == "RIGHT" }},
            "rotation_only_side_frames_new": ${rotationNew.count { it == "LEFT" || it == "RIGHT" }},
            "oscillation_flips_per_min_baseline": ${"%.1f".format(oldFlipsPerMin)},
            "oscillation_flips_per_min_new": ${"%.1f".format(newFlipsPerMin)},
            "recovery_to_keep_ms_baseline": ${(recOld.drop(4).indexOf("KEEP").takeIf { it >= 0 }?.times(150) ?: -1)},
            "recovery_to_keep_ms_new": ${(recNew.drop(4).indexOf("KEEP").takeIf { it >= 0 }?.times(150) ?: -1)},
            "corridor_unavailable_new_command": "${unavailable.last()}"
          }
        }
        """.trimIndent()
        val out = File("build/core_v1_metrics.json")
        out.parentFile.mkdirs()
        out.writeText(metrics)
        println(metrics)

        assertEquals("new false STOP cases", 0, newFalse.size)
        assertEquals("blocking recall", positives.size, newRecall)
        assertEquals("single-frame noise must not stop", 0, noiseNew)
        assertEquals("straight center must stay KEEP", true, straight.all { it == "KEEP" })
        assertTrue("true drift detected within 4 frames", firstSide(driftLeft) in 0..4)
        assertEquals("rotation alone must not emit LEFT/RIGHT", 0,
            rotationNew.count { it == "LEFT" || it == "RIGHT" })
        assertTrue("oscillation flips <= 3/min", newFlipsPerMin <= 3.0)
        assertTrue("recovery <= 2s", recoveryNewMs in 0..2_000)
    }
}
