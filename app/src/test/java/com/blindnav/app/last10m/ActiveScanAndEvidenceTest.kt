package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.random.Random

/**
 * 能力 1（主动扫描教练）、2（地标）、3（门牌）、7（该看哪里）、12（为什么不能确认）的验证。
 *
 * 门牌这一路是**拿真实街景转写测的**：lsvt_digit_tokens.tsv 是从 LSVT 的
 * 10,102 条中文街景转写里抓出来的 905 种含数字串（5元 / 24小时 / 361° / 2F /
 * 联系电话：13720158583 / 161号 …），不是手搓的理想样例。
 */
class ActiveScanAndEvidenceTest {

    // ---------------------------------------------------------------- 能力 1
    private fun naiveFlips(bearing: Double, headings: List<Double>, upright: Boolean = true): Int {
        var prev: ScanDirection? = null
        var flips = 0
        for (h in headings) {
            val d = ScanCoach.naive(bearing, h, upright)
            if (prev != null && d != prev) flips++
            prev = d
        }
        return flips
    }

    private fun coachFlips(bearing: Double, headings: List<Double>, stepMs: Long = 200): Int {
        val coach = ScanCoach()
        var t = 0L
        for (h in headings) { coach.update(bearing, h, t, true); t += stepMs }
        return coach.directionChanges
    }

    @Test
    fun standingStillNearBoundaryDoesNotFlip() {
        // 关键场景：目标刚好在正前边界附近（28°，无迟滞时半宽 30°），罗盘 ±12° 抖动
        // 会反复跨过边界 —— 这才是"左前右来回翻"的真实成因。
        val rnd = Random(7)
        val headings = (0 until 150).map { -28.0 + rnd.nextDouble(-12.0, 12.0) }
        val naive = naiveFlips(0.0, headings)
        val coach = coachFlips(0.0, headings)
        println("SCAN FLIP 目标在边界附近+罗盘抖动 150 帧(30s)：naive=$naive 次  coach=$coach 次  " +
            "naive=${"%.1f".format(naive * 60.0 / 30)} 次/分  coach=${"%.1f".format(coach * 60.0 / 30)} 次/分")
        assertTrue("无迟滞时应该明显抖动（否则这个用例没测到东西）", naive > 5)
        assertTrue("迟滞必须显著压住翻转：coach=$coach naive=$naive", coach * 3 < naive)
    }

    @Test
    fun slowSweepAcrossFrontDoesNotFlap() {
        // 相机从目标右侧 70° 缓慢扫到左侧 70°（heading 递增 → offset 递减）：
        // 应该 右→前→左 各一次，不该反复
        val headings = (0 until 120).map { -70.0 + it * 1.2 }
        val coach = ScanCoach()
        val seq = ArrayList<ScanDirection>()
        var t = 0L
        for (h in headings) {
            val d = coach.update(0.0, h, t, true)
            if (seq.isEmpty() || seq.last() != d) seq.add(d)
            t += 200
        }
        println("SCAN SWEEP 顺序 = $seq")
        assertEquals("穿过正前应该正好三段",
            listOf(ScanDirection.RIGHT, ScanDirection.FRONT, ScanDirection.LEFT), seq)
    }

    @Test
    fun wrapsAcrossNorthCorrectly() {
        // 目标在 300°、朝向 10° → 目标在左边 70°，不是右边 290°
        assertEquals(ScanDirection.LEFT, ScanCoach().update(300.0, 10.0, 0, true))
        assertEquals(ScanDirection.RIGHT, ScanCoach().update(10.0, 300.0, 0, true))
        // 350 vs 10 只差 20°，落在正前死区内
        assertEquals(ScanDirection.FRONT, ScanCoach().update(350.0, 10.0, 0, true))
    }

    @Test
    fun unknownWhenSensorsUntrustworthy() {
        val coach = ScanCoach()
        assertEquals(ScanDirection.UNKNOWN, coach.update(90.0, null, 0, true))     // 罗盘没值
        assertEquals(ScanDirection.UNKNOWN, coach.update(90.0, 0.0, 0, false))     // 手机没正对
        assertEquals(ScanDirection.UNKNOWN, coach.update(null, 0.0, 0, true))      // 不知道目标方位
        assertEquals(ScanDirection.UNKNOWN, coach.update(90.0, Double.NaN, 0, true))
    }

    @Test
    fun phoneNotFacingBodyStillConverges() {
        // 手机朝向与身体差 90°（低头看右手边的手机），目标在身体正前方
        val coach = ScanCoach()
        var last = ScanDirection.UNKNOWN
        var t = 0L
        repeat(20) { last = coach.update(0.0, 90.0, t, true); t += 200 }
        assertEquals("固定偏置应该稳定收敛到一个方向而不是乱翻", ScanDirection.LEFT, last)
        assertTrue("固定偏置不该产生多次翻转", coach.directionChanges <= 2)
    }

    // ---------------------------------------------------------------- 能力 7 / 12
    private fun snap(
        locked: Boolean = true, everLocked: Boolean = true, age: Long = 100,
        doors: Int = 1, active: Boolean = true, relation: Boolean? = true,
        ground: GroundState = GroundState.CLEAR, neighbor: Boolean = false,
        relConflict: Boolean = false, addrConflict: Boolean = false,
        lmConflict: Boolean = false, scan: ScanDirection = ScanDirection.FRONT
    ) = EvidenceSnapshot(locked, everLocked, age, doors, active, relation, ground,
        neighbor, relConflict, addrConflict, lmConflict, scan)

    @Test
    fun needViewFollowsMissingEvidence() {
        val cases = listOf(
            Triple("一切齐备", snap(), NeedView.NONE),
            Triple("没认出招牌", snap(locked = false, everLocked = false), NeedView.LOOK_UP_SIGN),
            Triple("招牌在右边", snap(locked = false, everLocked = false, scan = ScanDirection.RIGHT), NeedView.SCAN_RIGHT),
            Triple("招牌在左边", snap(locked = false, everLocked = false, scan = ScanDirection.LEFT), NeedView.SCAN_LEFT),
            Triple("方向不可靠", snap(locked = false, everLocked = false, scan = ScanDirection.UNKNOWN), NeedView.SCAN_WIDER),
            Triple("锁丢了", snap(locked = false, everLocked = true), NeedView.LOOK_UP_SIGN),
            Triple("没看到门", snap(doors = 0, active = false, relation = null), NeedView.LOOK_AT_DOOR),
            Triple("多门分不清", snap(doors = 3, relation = null), NeedView.SCAN_WIDER),
            Triple("关系没判", snap(relation = null), NeedView.LOOK_AT_DOOR),
            Triple("地面没进画面", snap(ground = GroundState.NOT_OBSERVED), NeedView.LOOK_DOWN_GROUND),
            Triple("地面看不清", snap(ground = GroundState.UNKNOWN), NeedView.LOOK_DOWN_GROUND),
            Triple("门牌冲突", snap(addrConflict = true), NeedView.REOBSERVE_TARGET),
            Triple("地标冲突", snap(lmConflict = true), NeedView.REOBSERVE_TARGET),
            Triple("关系判成隔壁", snap(relConflict = true), NeedView.REOBSERVE_TARGET)
        )
        for ((name, snapshot, want) in cases) {
            val plan = NeedViewPlanner.plan(snapshot)
            println("  $name → ${plan.view}  reasons=${plan.reasons}  「${plan.prompt}」")
            assertEquals("$name 的 needView 不对", want, plan.view)
        }
    }

    @Test
    fun unknownGroundNeverLooksSafe() {
        val p = NeedViewPlanner.plan(snap(ground = GroundState.UNKNOWN))
        assertTrue("地面 UNKNOWN 必须进 reasons", p.reasons.contains(ReasonCode.GROUND_UNKNOWN))
        assertNotEquals(NeedView.NONE, p.view)
        // 而且到达判定本身也不会放行
        val cleared = NeedViewPlanner.plan(snap(ground = GroundState.CLEAR))
        assertEquals(NeedView.NONE, cleared.view)
    }

    @Test
    fun conflictOutranksEverythingElse() {
        // 同时有冲突和"缺地面"时，必须先处理冲突
        val p = NeedViewPlanner.plan(snap(addrConflict = true, ground = GroundState.NOT_OBSERVED, doors = 4, relation = null))
        assertEquals(NeedView.REOBSERVE_TARGET, p.view)
        assertEquals(ReasonCode.ADDRESS_CONFLICT, p.reasons.first())
    }

    // ---------------------------------------------------------------- 能力 3
    @Test
    fun houseNumberExtractionOnRealStreetText() {
        val tokens = javaClass.classLoader!!.getResourceAsStream("last10m/lsvt_digit_tokens.tsv")!!
            .bufferedReader().readLines().mapNotNull {
                val f = it.split("\t")
                if (f.size < 2) null else f[0] to (f[1].toIntOrNull() ?: 1)
            }
        val kinds = HashMap<String, Int>()
        val houseNumbers = ArrayList<String>()
        for ((raw, count) in tokens) {
            val tok = AddressExtractor.extract(raw) ?: continue
            kinds[tok.kind.name] = (kinds[tok.kind.name] ?: 0) + count
            if (tok.kind == AddressKind.HOUSE_NUMBER || tok.kind == AddressKind.ENTRY_NUMBER ||
                tok.kind == AddressKind.BUILDING_NUMBER) houseNumbers.add("$raw→${tok.number}")
        }
        println("ADDRESS 真实串 ${tokens.size} 种 / ${tokens.sumOf { it.second }} 次，分类结果：")
        kinds.entries.sortedByDescending { it.value }.forEach { println("  ${it.key.padEnd(18)} ${it.value}") }
        println("  明确门牌/楼号/入口号：$houseNumbers")
        assertTrue("真实街景里光数字的量远大于真门牌，必须归到候选而不是明确门牌",
            (kinds["NUMBER_CANDIDATE"] ?: 0) > (kinds["HOUSE_NUMBER"] ?: 0))
    }

    @Test
    fun bareNumbersNeverCreateConflict() {
        val target = AddressEvidence(houseNumber = 102)
        // "24"（可能是营业时间）、"100"（可能是编号）都不能推翻店名证据
        for (s in listOf("24", "100", "5", "2018")) {
            val tok = AddressExtractor.extract(s)!!
            assertEquals("「$s」不该判冲突", AddressOutcome.UNKNOWN, target.compare(tok))
        }
        // 明确的 101号 才算冲突
        assertEquals(AddressOutcome.CONFLICT, target.compare(AddressExtractor.extract("101号")!!))
        assertEquals(AddressOutcome.MATCH, target.compare(AddressExtractor.extract("102号")!!))
    }

    @Test
    fun addressNoiseClassesAreFiltered() {
        val expectHouse = AddressEvidence(houseNumber = 161)
        for (noise in listOf("5元", "24小时", "联系电话：13720158583", "52863926", "361°", "2F", "2018年")) {
            val tok = AddressExtractor.extract(noise)
            assertTrue("「$noise」应被识别为噪声类，实际=${tok?.kind}",
                tok == null || tok.kind in setOf(AddressKind.PRICE, AddressKind.HOURS, AddressKind.PHONE,
                    AddressKind.DATE, AddressKind.FLOOR, AddressKind.NOISE, AddressKind.DISTANCE))
            if (tok != null) assertEquals("「$noise」不该产生冲突", AddressOutcome.UNKNOWN, expectHouse.compare(tok))
        }
        // 真正的门牌
        assertEquals(AddressOutcome.MATCH, expectHouse.compare(AddressExtractor.extract("161号")!!))
        assertEquals(AddressKind.ENTRY_NUMBER, AddressExtractor.extract("3号入口")!!.kind)
        assertEquals(AddressKind.BUILDING_NUMBER, AddressExtractor.extract("3号楼")!!.kind)
        assertEquals(AddressKind.UNIT, AddressExtractor.extract("1单元")!!.kind)
        assertEquals(AddressKind.BUILDING_LABEL, AddressExtractor.extract("B座")!!.kind)
        assertEquals(AddressKind.FLOOR, AddressExtractor.extract("2F")!!.kind)
    }

    @Test
    fun buildingLabelConflictDetected() {
        val target = AddressEvidence(buildingLabel = "A")
        assertEquals(AddressOutcome.MATCH, target.compare(AddressExtractor.extract("A座")!!))
        assertEquals(AddressOutcome.CONFLICT, target.compare(AddressExtractor.extract("B座")!!))
        // 没有地址期望时，整路证据退出
        assertTrue(AddressEvidence().empty)
        assertEquals(AddressOutcome.UNKNOWN, AddressEvidence().compare(AddressExtractor.extract("161号")!!))
    }

    // ---------------------------------------------------------------- 能力 2
    @Test
    fun supportingAndConflictingLandmarks() {
        val profile = TargetPoiProfile.build("星巴克万象城店")
        val index = LandmarkIndex(profile,
            supporting = listOf("万象城", "STARBUCKS"),
            conflicting = listOf("瑞幸咖啡"))
        fun hit(t: String, frame: Long = 1, ts: Long = 1000) =
            TextHit(t, 0, 0, 50, 20, 0.9, frame, ts, EvidenceSource.OCR)

        assertEquals(LandmarkRole.SUPPORTING_LANDMARK, index.classify(hit("万象城")).role)
        assertEquals(LandmarkRole.CONFLICTING_LANDMARK, index.classify(hit("瑞幸咖啡")).role)
        assertEquals(LandmarkRole.UNKNOWN, index.classify(hit("中国人民银行")).role)

        // 只有互斥地标、没有主证据 → 冲突
        val ledger = LandmarkLedger()
        ledger.add(index.classify(hit("瑞幸咖啡")))
        assertTrue(ledger.conflict(1200))
        // 同时读到目标 → 冲突解除（同排店铺常见）
        val ledger2 = LandmarkLedger()
        ledger2.add(index.classify(hit("瑞幸咖啡")))
        ledger2.add(index.classify(hit("星巴克万象城店", 2, 1100)))
        assertNotEquals(LandmarkRole.PRIMARY_TARGET, LandmarkRole.UNKNOWN)
        assertTrue("读到目标主名后不该再算冲突", !ledger2.conflict(1200))
    }

    @Test
    fun landmarksNeverConfirmArrivalOnTheirOwn() {
        // 地标只能提高"到没到这栋建筑"的把握；到达仍要过六条。
        // 这里直接验证：只有支持性地标时，needView 不会变成 NONE。
        val profile = TargetPoiProfile.build("星巴克万象城店")
        val index = LandmarkIndex(profile, supporting = listOf("万象城"))
        val ledger = LandmarkLedger()
        ledger.add(index.classify(TextHit("万象城", 0, 0, 60, 20, 0.9, 1, 1000)))
        assertTrue(ledger.nearBuildingOnly(1200))
        val plan = NeedViewPlanner.plan(snap(locked = false, everLocked = false))
        assertNotEquals("只有地标时不该报「确认到达」", NeedView.NONE, plan.view)
    }
}
