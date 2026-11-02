package com.blindnav.app.regression

import com.blindnav.app.WalkingSafetyPolicy
import com.blindnav.app.last10m.ArrivalAuthority
import com.blindnav.app.last10m.ArrivalDecision
import com.blindnav.app.last10m.EntranceCandidate
import com.blindnav.app.last10m.EvidenceSource
import com.blindnav.app.last10m.GroundEvidence
import com.blindnav.app.last10m.GroundSource
import com.blindnav.app.last10m.TargetLock
import com.blindnav.app.last10m.TargetPoiProfile
import com.blindnav.app.last10m.TextHit
import com.blindnav.app.scene.TextKind
import com.blindnav.app.scene.TextSaliencePolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Permanent regressions from the 2026-11-03 desktop core audit. */
class CoreRegressionTest {

    private fun policyInput(
        now: Long,
        corridor: Boolean,
        hits: List<WalkingSafetyPolicy.Hit>,
        nearFocus: Boolean,
        walking: Boolean,
    ) = WalkingSafetyPolicy.Input(
        nowMs = now,
        corridorAvailable = corridor,
        corridorSide = "center",
        corridorCurve = "none",
        corridorScore = 0.9f,
        hits = hits,
        nearFocus = nearFocus,
        nearFocusSinceMs = if (nearFocus) 0L else 0L,
        walking = walking,
        rotationSuspected = false,
    )

    @Test
    fun reg001_staticDeskDoesNotKeepStopping() {
        val p = WalkingSafetyPolicy()
        repeat(10) { i ->
            val d = p.evaluate(policyInput(1000L + i * 150L, true, emptyList(), true, false))
            assertNotEquals("static desk must not STOP", "STOP", d.risk)
        }
    }

    @Test
    fun reg002_kfcLocksWithinTwoFrames() {
        val lock = TargetLock(TargetPoiProfile.build("肯德基", extraAliases = listOf("KFC")))
        val first = lock.observe(TextHit("KFC", 0, 0, 100, 40, 0.95, 1, 1000), 1000)
        assertFalse("single frame must not lock", first.status == TargetLock.Status.TARGET_LOCKED)
        lock.observe(TextHit("KFC", 0, 0, 100, 40, 0.95, 2, 1200), 1200)
        assertTrue("two distinct frames lock the target", lock.isLocked())
    }

    @Test
    fun reg003_kfcIsSilentWhenTargetIsLibrary() {
        val library = TextSaliencePolicy("图书馆", listOf("图书馆"))
        val d = library.consider("KFC", 1000)
        assertEquals(TextKind.GENERAL, d.kind)
        assertFalse("KFC is not salient when target is library", d.speak)

        val kfc = TextSaliencePolicy("肯德基", listOf("KFC"))
        assertTrue("KFC is salient when target is KFC", kfc.consider("KFC", 1000).speak)
        assertFalse("same text within 10s is deduped", kfc.consider("KFC", 2000).speak)
    }

    @Test
    fun reg004_wallCloseDoesNotStop() {
        val p = WalkingSafetyPolicy()
        repeat(10) { i ->
            val d = p.evaluate(policyInput(1000L + i * 150L, false, emptyList(), true, false))
            assertNotEquals("close wall alone must not STOP", "STOP", d.risk)
        }
    }

    @Test
    fun reg005_unknownObjectInCorridorStops() {
        val p = WalkingSafetyPolicy()
        var stopped = false
        repeat(10) { i ->
            val d = p.evaluate(policyInput(1000L + i * 150L, true,
                listOf(WalkingSafetyPolicy.Hit("障碍", "近", 0.8f, 0.9f)), false, true))
            if (d.risk == "STOP") stopped = true
        }
        assertTrue("persistent unknown obstacle in corridor must STOP", stopped)
    }

    @Test
    fun reg006_neighborDoorNeverArrives() {
        val authority = ArrivalAuthority()
        val door = EntranceCandidate(
            doorId = "neighbor",
            left = 0, top = 0, right = 100, bottom = 200,
            firstSeenMs = 1000, lastSeenMs = 2000, seenFrames = 4,
            relationConfirmed = false,
            relationSource = EvidenceSource.LOCAL_MODEL,
            groundObserved = true,
            groundEvidence = GroundEvidence(
                visible = true, step = false, curb = false, obstruction = false,
                sourceFrameId = 4, timestampMs = 2000, source = GroundSource.GROUND_MODEL,
                sessionId = "s1",
            ),
            sessionId = "s1",
        )
        val decision = authority.decide(ArrivalAuthority.Input(
            nowMs = 2000, targetLocked = true, targetEverLocked = true, targetAgeMs = 100,
            activeDoor = door, neighborConflict = true, relationConflict = false,
            facts = null, blockingConflict = false, sessionId = "s1",
        ))
        assertTrue("must not confirm arrival for a neighbor door",
            decision.outcome in setOf(ArrivalDecision.RELATION_CONFLICT, ArrivalDecision.REOBSERVE_DOOR))
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, decision.outcome)
    }
}
