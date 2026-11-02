package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 身份安全：别名分档（EXPLICIT / DERIVED_SAFE / DERIVED_WEAK）与 480 条真实隔壁名硬负样本。
 *
 * 上一轮实测的结论是：**危险不在模糊阈值，在别名规则**。
 * 阈值扫到 0.75 时真实隔壁名的模糊误接受已经是 0.00%，端到端却仍放行 4/480 = 0.83%，
 * 四条全部来自 `ALIAS`，成因是"去后缀派生别名 + 包含即算别名"：
 *   车主服务中心 → 车主、便利超市 → 便利、抚顺银行 → 抚顺、晋商银行 → 晋商
 * 于是任何写着"车主X / 抚顺X"的隔壁招牌都能以 ALIAS 0.95 够格确认身份。
 *
 * 这一版不许用"提高 fuzzy 阈值"绕过（那不是问题所在）。规则改成：
 *  - 自动派生的 ≤2 字别名、或命中通用业态词的别名 ⇒ `DERIVED_WEAK`：**只算佐证，不确认身份**；
 *  - 只读到名字的一小段碎片（<4 字且占比 <0.6）⇒ 同样只算佐证；
 *  - 人工明确给出的别名（`extraAliases`）⇒ `EXPLICIT`，短也允许确认身份。
 *
 * 同时必须把代价摊开：这样改会让多少正样本失去身份档。数字在下面打印，
 * 不藏——如果 recall 掉了，报告里就得有这一行。
 */
class Last10mAliasSafetyTest {

    private fun identity(poi: String, text: String): Boolean {
        val p = TargetPoiProfile.build(poi)
        val m = p.match(text) ?: return false
        return p.acceptsAsIdentity(m)
    }

    // ---------------------------------------------------------------- 硬负样本
    @Test
    fun adjacentStoreHardNegativesAreNotIdentity() {
        val neg = StoreCorpus.adjacentNegatives()
        val accepted = ArrayList<Triple<String, String, TargetMatch>>()
        for ((poi, text, _) in neg) {
            val p = TargetPoiProfile.build(poi)
            val m = p.match(text) ?: continue
            if (p.acceptsAsIdentity(m)) accepted.add(Triple(poi, text, m))
        }
        println("ALIAS HARD NEGATIVES 真实隔壁名 ${neg.size} 条，端到端身份放行 ${accepted.size} " +
            "= ${"%.2f".format(100.0 * accepted.size / neg.size)}%")
        accepted.take(12).forEach { (p, t, m) ->
            println("  仍在放行：目标「$p」 看到「$t」 → ${m.kind} 凭什么=${m.matchedAlias} " +
                "aliasSource=${m.aliasSource}")
        }
        assertEquals("真实隔壁名不得被确认成目标身份", 0, accepted.size)
    }

    @Test
    fun sharedPrefixNeighboursAreDowngradedToSupporting() {
        // 这四条就是上一轮 0.83% 误接受的全部来源。改档之后必须变成"只算佐证"。
        for ((poi, other) in listOf(
            "车主服务中心" to "车主火锅", "便利超市" to "便利火锅",
            "抚顺银行" to "抚顺火锅", "晋商银行" to "晋商火锅")) {
            val p = TargetPoiProfile.build(poi)
            val m = p.match(other)
            assertTrue("「$poi」看到「$other」不该完全没有匹配（碎片确实相同，是有效佐证）", m != null)
            assertFalse("「$poi」←「$other」 不得算身份", p.acceptsAsIdentity(m!!))
            println("  $poi ← $other  ${m.kind} score=${"%.2f".format(m.score)} " +
                "alias=${m.matchedAlias} aliasSource=${m.aliasSource}")
        }
    }

    @Test
    fun derivedAliasClassificationIsExplainedByRules() {
        val p = TargetPoiProfile.build("抚顺银行")
        val entries = p.aliases.mapIndexed { i, a -> a to p.aliasSources.getOrElse(i) { AliasSource.DERIVED_WEAK } }
        println("  抚顺银行 派生别名与档位：$entries  weak=${p.weakAliases} strong=${p.strongAliases}")
        assertTrue("≤2 字的派生别名必须归 DERIVED_WEAK",
            entries.any { it.second == AliasSource.DERIVED_WEAK && it.first.length <= 2 })
        // 主名不在 aliases 里（它在 matchForms），所以"够格"要由主名匹配来证明
        val full = p.match("抚顺银行")
        assertNotNull("主名必须仍然能匹配", full)
        assertEquals(TargetMatchKind.EXACT, full!!.kind)
        assertTrue("主名命中永远够格确认身份", p.acceptsAsIdentity(full))
        assertFalse("短派生别名不许进强档", p.strongAliases.any { it.length <= 2 })
    }

    @Test
    fun fragmentRuleRejectsAnotherStoresWholeName() {
        // 「天地壹号」与「天地壹」在语料里是两家真实存在的店。
        // 碎片方向必须把这种"剩下的部分不是噪声"的情况降级成佐证。
        val p = TargetPoiProfile.build("天地壹号")
        val m = p.match("天地壹")
        assertTrue("碎片「天地壹」不该被当成主名的一部分直接确认身份",
            m == null || !p.acceptsAsIdentity(m))
        println("  天地壹号 ← 天地壹 = ${m?.kind} aliasSource=${m?.aliasSource}")
        // 反过来：主名完整出现仍然够格
        assertTrue(p.acceptsAsIdentity(p.match("天地壹号")!!))
        // 后缀是噪声时才允许短碎片确认：这条把尺度写清楚，避免以后被随手放宽
        assertTrue("剩下的是业态噪声词时，3 字品牌主名算身份",
            TargetPoiProfile.isStrongFragment("星巴克咖啡", "星巴克"))
        assertFalse("剩下的是一个真实品牌成分时，短碎片只算佐证",
            TargetPoiProfile.isStrongFragment("天地壹号", "天地壹"))
        assertFalse("2 字碎片永远不够格（'抚顺银行'←'抚顺' 就是这一类）",
            TargetPoiProfile.isStrongFragment("抚顺银行", "抚顺"))
    }

    // ---------------------------------------------------------------- 代价摊开
    @Test
    fun identityRecallTradeoffIsReported() {
        val positives = StoreCorpus.positives()
        val byKind = LinkedHashMap<String, IntArray>()   // kind -> [total, identity, matched]
        val examples = ArrayList<String>()
        for ((poi, text, kind) in positives) {
            val cell = byKind.getOrPut(kind) { intArrayOf(0, 0, 0) }
            cell[0] += 1
            val p = TargetPoiProfile.build(poi)
            val m = p.match(text)
            if (m != null) cell[2] += 1
            if (m != null && p.acceptsAsIdentity(m)) cell[1] += 1
            else if (examples.size < 14) examples.add("  失去身份档：$kind 「$poi」←「$text」 ${m?.kind ?: "null"}")
        }
        val total = byKind.values.sumOf { it[0] }
        val identity = byKind.values.sumOf { it[1] }
        println("ALIAS TRADEOFF positives=$total 身份档=$identity " +
            "(${ "%.1f".format(100.0 * identity / total)}%)")
        byKind.forEach { (k, v) ->
            println(String.format("  %-14s n=%-4d 身份档=%-4d (%5.1f%%)  仍能匹配=%d (%5.1f%%)",
                k, v[0], v[1], 100.0 * v[1] / v[0], v[2], 100.0 * v[2] / v[0]))
        }
        examples.forEach { println(it) }

        // 断言分两类，而不是一个笼统的百分比：
        // (a) 招牌上确实写着完整名字的变体，必须几乎全部够格——否则门口认不出目标；
        // (b) 截字/错字这类"只读到一部分"的变体，允许降级成佐证（这正是本次收紧的目的），
        //     但它们仍然必须被匹配到，系统要知道"这可能是目标"。
        for (k in listOf("clean", "suffix", "space_suffix", "fullwidth_digit", "lowercase", "padded")) {
            val v = byKind[k] ?: continue
            assertTrue("「$k」类变体的身份档召回低于 95%（${v[1]}/${v[0]}），规则收坏了",
                v[1] >= v[0] * 0.95)
        }
        for (k in listOf("drop_last", "drop_first", "wrong_char")) {
            val v = byKind[k] ?: continue
            assertTrue("「$k」类必须仍然被匹配到（${v[2]}/${v[0]}），否则连候选都不报了",
                v[2] >= v[0] * 0.90)
        }
    }

    @Test
    fun explicitShortAliasStillConfirmsIdentity() {
        // 人工明确给的短别名必须仍然可用——这是 EXPLICIT 与 DERIVED 的区别，
        // 也是"为什么不干脆禁止短别名"的答案：禁止的是**自动派生**，不是人的判断。
        val manual = TargetPoiProfile.build("星巴克咖啡", listOf("星巴克", "STARBUCKS"))
        val m = manual.match("星巴克")
        assertNotNull(m)
        assertEquals(AliasSource.EXPLICIT, m!!.aliasSource)
        assertTrue("人工给的短别名仍算身份", manual.acceptsAsIdentity(m))
        val auto = TargetPoiProfile.build("旺角小食店")
        val autoMatch = auto.match("旺角")
        assertTrue("同一批字，自动派生/碎片命中只能当佐证",
            autoMatch == null || !auto.acceptsAsIdentity(autoMatch))
        println("EXPLICIT vs DERIVED  「星巴克咖啡」←「星巴克」=${m.kind} identity=true ; " +
            "「旺角小食店」←「旺角」=${autoMatch?.kind ?: "null"}")
    }

    // ---------------------------------------------------------------- TargetLock 保护
    private fun hit(text: String, frame: Long, ts: Long) =
        TextHit(text, 10, 10, 120, 40, 0.9, frame, ts, EvidenceSource.OCR)

    @Test
    fun singleFrameExplicitAliasStillCannotLock() {
        // 保护 A：一次命中，即使是人工明确给的别名，也不许单帧锁定。
        val profile = TargetPoiProfile.build("星巴克咖啡", listOf("星巴克", "STARBUCKS"))
        val lock = TargetLock(profile)
        lock.observe(hit("星巴克", 1, 1_000), 1_000)
        assertEquals("单帧不得锁定", TargetLock.Status.SEARCH_TARGET, lock.snapshot().status)
        lock.observe(hit("星巴克", 1, 1_100), 1_100)
        assertEquals("同一帧内重复命中不算第二帧",
            TargetLock.Status.SEARCH_TARGET, lock.snapshot().status)
        lock.observe(hit("星巴克", 2, 1_400), 1_400)
        assertEquals("跨两帧才允许锁定",
            TargetLock.Status.TARGET_LOCKED, lock.snapshot().status)
    }

    @Test
    fun weakAliasRepeatedAcrossFramesCannotLockAlone() {
        // 保护 B：DERIVED_WEAK 连续出现也不许独立确认身份——必须再有主名/人工别名/独立来源。
        val profile = TargetPoiProfile.build("抚顺银行")
        assertTrue("测试前提：抚顺 必须是弱别名", profile.weakAliases.contains("抚顺"))
        val lock = TargetLock(profile)
        for (f in 1..6) lock.observe(hit("抚顺", f.toLong(), 1_000L + f * 200), 1_000L + f * 200)
        val snap = lock.snapshot()
        assertEquals("弱别名刷多少帧都不该锁定", TargetLock.Status.SEARCH_TARGET, snap.status)
        assertTrue("但弱命中要留痕，面板才答得出'看到过什么'", snap.supportingHits > 0)
        assertEquals("佐证据不能冒充 matchedAlias", "", snap.matchedAlias)
        // 再给一次主名命中（强证据），此时才开始计数
        lock.observe(hit("抚顺银行", 10, 3_000), 3_000)
        assertTrue("主名命中才开始积累身份证据", lock.snapshot().hitCount >= 1)
        lock.observe(hit("抚顺银行", 11, 3_200), 3_200)
        assertEquals("主名跨两帧才允许锁定", TargetLock.Status.TARGET_LOCKED, lock.snapshot().status)
    }
}
