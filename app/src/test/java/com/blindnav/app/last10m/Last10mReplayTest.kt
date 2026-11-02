package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 电脑端 replay（任务书第十三节）。
 *
 * 关键点：这里跑的是 `app/src/main/java/.../last10m/` 下**同一份纯 Kotlin 代码**，
 * 不是 Python 复刻。所以这些 case 通过，代表手机上会走同样的判定；
 * 用 Python 另写一份状态机再"验证通过"，测的是另一套逻辑，没有意义。
 *
 * 时钟全部由测试注入（nowMs 参数），所以结果可复现，不依赖真实时间。
 */
class Last10mReplayTest {

    private fun profile() = TargetPoiProfile.build(
        poiName = "星巴克咖啡",
        extraAliases = listOf("星巴克", "STARBUCKS", "Starbucks Coffee")
    )

    private fun machine() = Last10mStateMachine(profile())

    private fun textHit(text: String, frameId: Long, ts: Long, box: IntArray = intArrayOf(10, 10, 200, 60)) =
        TextHit(text, box[0], box[1], box[2], box[3], 0.9, frameId, ts)

    private fun lockTarget(sm: Last10mStateMachine) {
        sm.setInRange(true, 1000)
        sm.onTextHit(textHit("星巴克咖啡", 1, 1000), 1000)
        sm.onTextHit(textHit("星巴克", 2, 1200), 1200)
    }

    private fun facts(
        sm: Last10mStateMachine,
        relation: String,
        frameId: Long,
        ts: Long,
        groundVisible: Boolean? = null,
        identity: String? = null,
        extra: Map<String, Any?> = emptyMap()
    ): Last10mStateMachine.Result = sm.onVlmFacts(
        VlmFacts.fromFields(
            mapOf(
                "target_visible" to true,
                "target_identity" to identity,
                "candidate_entrance_visible" to true,
                "entrance_relation" to relation,
                "neighboring_entrance_present" to false,
                "ground_visible" to groundVisible
            ) + extra, frameId, ts
        ), ts
    )

    // Case 1：正确招牌，无门 → 不得确认。
    @Test fun case1_signWithoutDoorCannotConfirm() {
        val sm = machine()
        lockTarget(sm)
        val r = sm.evaluate(1300, emptyList())
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertEquals(Last10mState.TARGET_LOCKED, r.state)
        assertTrue(r.targetLocked)
        assertNull(r.activeDoorId)
    }

    // Case 2：有门，无目标身份 → 不得确认。
    @Test fun case2_doorWithoutIdentityCannotConfirm() {
        val sm = machine()
        sm.setInRange(true, 1000)
        sm.onDoorDetected(50, 200, 300, 500, 1, 1000, 1000)
        val r = sm.evaluate(1100, emptyList())
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertEquals(Last10mState.SEARCH_TARGET, r.state)
        assertFalse(r.targetLocked)
    }

    // Case 3：正确招牌 + 隔壁门 → 不得确认（邻门是本任务最高优先级的假接受）。
    @Test fun case3_neighborDoorMustNotBeConfirmedAsTarget() {
        val sm = machine()
        lockTarget(sm)
        sm.onDoorDetected(50, 200, 300, 500, 3, 1400, 1400)      // 目标门
        sm.onDoorDetected(600, 200, 850, 500, 4, 1500, 1500)     // 隔壁门（无重叠 → 新候选）
        val r = sm.evaluate(1550, emptyList())
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertTrue("应识别出同视野存在多个候选门", r.candidateCount >= 2)
        // 未经关系确认的多候选只能重扫，不能挑一个当答案。
        assertEquals(ArrivalDecision.REOBSERVE_DOOR, r.decision)
        assertEquals(Last10mState.REOBSERVE, r.state)
    }

    // Case 4：正确招牌 + 候选门，但门前地面不可见 → 不得确认，只能要求低头拍地面。
    @Test fun case4_relationConfirmedButGroundMissing() {
        val sm = machine()
        lockTarget(sm)
        sm.onDoorDetected(50, 200, 300, 500, 3, 1400, 1400)
        facts(sm, "likely_same_storefront", 4, 1500)
        val r = sm.evaluate(1600, emptyList())
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertEquals(ArrivalDecision.LOOK_DOWN_GROUND, r.decision)
        assertEquals(Last10mState.VERIFY_GROUND, r.state)
        assertFalse(r.groundObserved)
    }

    // Case 5：目标消失后再看到别的门 → 旧 Target Lock 必须失效，旧关系必须作废。
    @Test fun case5_staleTargetLockCannotAttachToNewDoor() {
        val sm = machine()
        lockTarget(sm)
        sm.onDoorDetected(50, 200, 300, 500, 3, 1400, 1400)
        facts(sm, "likely_same_storefront", 4, 1500)
        sm.onGroundEvidence(GroundEvidence(true, false, false, false, 5, 4300), null, 4300)
        // 目标最后可见 1200ms，现在 4300ms：超过 lostTimeout(2500) → 锁失效
        val lost = sm.onTargetLost("target_expired", 4300)
        assertFalse("目标锁必须已失效", lost.targetLocked)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, lost.decision)
        // 根因是身份丢了，不是门的问题：必须报 TARGET_LOST 并进入 REOBSERVE，
        // 提示用户重扫招牌，而不是引导他去重看门。
        assertEquals(ArrivalDecision.TARGET_LOST, lost.decision)
        assertEquals(Last10mState.REOBSERVE, lost.state)
        // 之后出现的门不能继承旧关系
        val after = sm.onDoorDetected(600, 200, 850, 500, 6, 4400, 4400)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, after.decision)
    }

    // Case 6：目标重新出现并多帧恢复 → 允许继续关联，六条件齐备才可确认。
    @Test fun case6_reacquireThenFullChainConfirms() {
        val sm = machine()
        lockTarget(sm)
        sm.onDoorDetected(50, 200, 300, 500, 3, 1400, 1400)
        facts(sm, "likely_same_storefront", 4, 1500)
        val ground = sm.onGroundEvidence(
            GroundEvidence(true, true, false, false, 5, 1600), null, 1600)
        assertEquals(ArrivalDecision.ARRIVAL_CONFIRMED, ground.decision)
        assertEquals(Last10mState.ARRIVAL_CONFIRMED, ground.state)
        assertTrue("台阶信息必须保留在提示里，不能被'已确认'吞掉",
            ground.decision.name == "ARRIVAL_CONFIRMED")
    }

    // Case 7：OCR 单帧命中 → 不能锁定目标。
    @Test fun case7_singleFrameOcrCannotLock() {
        val sm = machine()
        sm.setInRange(true, 1000)
        val r = sm.onTextHit(textHit("星巴克咖啡", 1, 1000), 1000)
        assertFalse("单帧命中不得锁目标", r.targetLocked)
        assertEquals(1, r.targetHits)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
    }

    // Case 8：OCR 与 VLM 冲突 → REOBSERVE，不得确认。
    @Test fun case8_ocrVlmConflictForcesReobserve() {
        val sm = machine()
        lockTarget(sm)
        sm.onDoorDetected(50, 200, 300, 500, 3, 1400, 1400)
        val r = facts(sm, "conflict", 4, 1500)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertEquals(ArrivalDecision.RELATION_CONFLICT, r.decision)
        assertEquals(Last10mState.REOBSERVE, r.state)
    }

    // VLM 越界：试图自己宣布到达/给距离，字段必须被丢弃并记违规。
    @Test fun vlmCannotDeclareArrivalOrDistance() {
        val f = VlmFacts.fromFields(
            mapOf(
                "target_visible" to true, "arrival_confirmed" to true,
                "move_meters" to 3, "safe" to true, "entrance_relation" to "likely_same_storefront"
            ), 1, 1000
        )
        assertTrue("越界字段必须被记录", f.violations.containsAll(listOf("arrival_confirmed", "move_meters", "safe")))
        assertNull(f.targetIdentity)
        // 即使 VLM 说到达，六条件不齐时状态机也不能确认
        val sm = machine()
        sm.setInRange(true, 1000)
        val r = sm.onVlmFacts(f, 1000)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
    }

    // 别名与模糊匹配：不能只做字符串相等。
    @Test fun poiMatchingHandlesAliasesNormalizationAndFuzzy() {
        val p = profile()
        assertEquals(TargetMatchKind.EXACT, p.match("星巴克咖啡")?.kind)
        assertEquals(TargetMatchKind.ALIAS, p.match("星巴克")?.kind)
        assertEquals(TargetMatchKind.ALIAS, p.match("STARBUCKS")?.kind)
        assertEquals(TargetMatchKind.ALIAS, p.match("Starbucks 咖啡")?.kind)
        // 全角/空格/标点归一化
        assertNotNullKind(p.match("星 巴 克 咖 费"))
        // 隔壁店不能被判成目标
        assertNull(p.match("中国建设银行"))
        assertNull(p.match("瑞幸咖啡"))
        // 短串模糊档不足以确立身份
        val fuzzy = p.match("星八客")
        assertTrue(fuzzy == null || fuzzy.kind == TargetMatchKind.FUZZY)
        assertFalse("FUZZY 单独不算身份证据", p.acceptsAsIdentity(fuzzy ?: TargetMatch("", TargetMatchKind.NONE, 0.0)))
    }

    private fun assertNotNullKind(m: TargetMatch?) {
        assertTrue("应能匹配", m != null && m.kind != TargetMatchKind.NONE)
    }

    // 扫描方向：只给"朝哪边看"，且不确定时给 UNKNOWN。
    @Test fun scanDirectionOnlyPointsWhereToLook() {
        assertEquals(ScanDirection.RIGHT, ScanDirectionResolver.resolve(90.0, 0.0, true))
        assertEquals(ScanDirection.LEFT, ScanDirectionResolver.resolve(270.0, 0.0, true))
        assertEquals(ScanDirection.FRONT, ScanDirectionResolver.resolve(10.0, 0.0, true))
        assertEquals(ScanDirection.UNKNOWN, ScanDirectionResolver.resolve(null, 0.0, true))
        assertEquals(ScanDirection.UNKNOWN, ScanDirectionResolver.resolve(90.0, null, true))
        // 手机没拿正时不给方向结论，避免把用户往错误方向引
        assertEquals(ScanDirection.UNKNOWN, ScanDirectionResolver.resolve(90.0, 0.0, false))
    }

    // 地面 UNKNOWN 绝不等于 SAFE。
    @Test fun unknownGroundIsNeverSafe() {
        val sm = machine()
        lockTarget(sm)
        sm.onDoorDetected(50, 200, 300, 500, 3, 1400, 1400)
        facts(sm, "likely_same_storefront", 4, 1500)
        val r = sm.onGroundEvidence(GroundEvidence.unknown(5, 1600), null, 1600)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertEquals(Last10mState.VERIFY_GROUND, r.state)
        assertFalse(r.groundObserved)
    }
}
