package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 第 13 节：模糊阈值扫描（0.60 … 0.95）。
 *
 * 语料来自 [StoreCorpus]（LSVT 真实中文街景招牌转写里的 198 个名字），
 * 里面天然有"中国工商银行 / 中国建设银行""海幢街道工作委员会 / 站前街道工作委员会"
 * 这种只差两个字的隔壁店对——正是"把隔壁认成目标"最容易发生的地方。
 *
 * 两条曲线分开看：
 *  (1) 模糊档本身：阈值 T 下的 Recall / False Match Rate。
 *  (2) 端到端身份门 `acceptsAsIdentity()`：这条**不受 T 影响**，
 *      因为 EXACT/ALIAS/SUBSTRING 在模糊档之前就返回了。
 *      如果 (2) 的误接受高，问题在别名规则，不在阈值。
 *
 * 关键修正（本轮）：端到端那一栏以前是拿 `build(poi, 派生别名当人工别名传进去)` 算的。
 * 自从生产区分 `EXPLICIT / DERIVED_SAFE / DERIVED_WEAK` 之后，那种传法等于
 * 把派生别名全部当"人工明确给的"，测出来的就不是生产行为。现在端到端一律走
 * `TargetPoiProfile.build(poi)` 这条生产路径；另外单独给一栏"人工别名对照"，
 * 用来说明"人工明确给的短别名仍然可以确认身份"这个设计选择。
 */
class FuzzyThresholdSweepTest {

    private val names get() = StoreCorpus.names

    /** 与 TargetPoiProfile.match() 里模糊档那段同构，只是把门限拿出来。 */
    private fun fuzzyBest(candidate: String, forms: List<String>): Double {
        val cand = TextNormalizer.stripNoise(TextNormalizer.normalize(candidate))
        if (cand.length < TextNormalizer.FUZZY_MIN_LENGTH) return 0.0
        var best = 0.0
        for (value in forms) {
            if (value.length < TextNormalizer.FUZZY_MIN_LENGTH) continue
            best = maxOf(best, TextNormalizer.similarity(value, cand))
        }
        return best
    }

    private fun formsOf(poi: String, aliases: List<String>): List<String> =
        (listOf(poi) + aliases).map { TextNormalizer.stripNoise(TextNormalizer.normalize(it)) }

    /** 生产路径：只给 POI 名，别名由生产规则派生并按强度分档。 */
    private fun identityAcceptedProduction(poi: String, text: String): Boolean {
        val profile = TargetPoiProfile.build(poi)
        val m = profile.match(text) ?: return false
        return profile.acceptsAsIdentity(m)
    }

    /** 对照组：把派生别名当"人工明确给的"别名传进去（即修改前的行为）。 */
    private fun identityAcceptedWithExplicitAliases(poi: String, text: String): Boolean {
        val derived = TargetPoiProfile.build(poi).aliases
        val profile = TargetPoiProfile.build(poi, derived)
        val m = profile.match(text) ?: return false
        return profile.acceptsAsIdentity(m)
    }

    @Test
    fun replicaAgreesWithProductionAtShippedThreshold() {
        // 复刻必须先在 0.75 上和生产对齐，不然扫描曲线是编的。
        // 注意方向：生产是"命中前面任一档就提前返回"，所以
        //   生产给 FUZZY  ⟹ 复刻必须也说该被模糊档接住
        //   复刻说接住  ⟹ 生产必须给了某种匹配（可能落在更早的 EXACT/ALIAS/SUBSTRING 档）
        var fuzzyMissed = 0
        var replicaSaysButNoMatch = 0
        var earlierStageShadowed = 0
        val samples = names.take(60)
        for (poi in samples) {
            val forms = formsOf(poi, TargetPoiProfile.build(poi).aliases)
            for (other in samples.shuffled(java.util.Random(3)).take(8)) {
                val produced = TargetPoiProfile.build(poi).match(other)
                val mine = fuzzyBest(other, forms) >= TextNormalizer.FUZZY_THRESHOLD
                when {
                    produced?.kind == TargetMatchKind.FUZZY && !mine -> fuzzyMissed++
                    mine && produced == null -> replicaSaysButNoMatch++
                    mine && produced != null && produced.kind != TargetMatchKind.FUZZY ->
                        earlierStageShadowed++
                }
            }
        }
        println("REPLICA CHECK fuzzyMissed=$fuzzyMissed replicaSaysButNoMatch=$replicaSaysButNoMatch " +
            "earlierStageShadowed=$earlierStageShadowed")
        assertEquals("生产判 FUZZY 的样本复刻必须也接住", 0, fuzzyMissed)
        assertEquals("复刻接住但生产完全不匹配的样本不应存在", 0, replicaSaysButNoMatch)
    }

    @Test
    fun sweepThresholds() {
        val positives = StoreCorpus.positives()
        val negatives = StoreCorpus.negatives()
        val realNeg = StoreCorpus.adjacentNegatives()

        val thresholds = listOf(0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
        val lines = StringBuilder()
        lines.appendLine(String.format("%-6s %-8s %-14s %-14s %s",
            "阈值", "Recall", "FMR(真实隔壁名)", "FMR(全部负样本)", "端到端身份误接受(真实隔壁名)"))
        val endToEnd = realNeg.count { (p, t, _) -> identityAcceptedProduction(p, t) }
        for (th in thresholds) {
            val pr = positives.count { (p, t, _) -> fuzzyBest(t, formsOf(p, aliasesOf(p))) >= th }
            val frAll = negatives.count { (p, t, _) -> fuzzyBest(t, formsOf(p, aliasesOf(p))) >= th }
            val frReal = realNeg.count { (p, t, _) -> fuzzyBest(t, formsOf(p, aliasesOf(p))) >= th }
            lines.appendLine(String.format("%.2f   %5.1f%%    %6.2f%%         %6.2f%%          %6.2f%%",
                th, 100.0 * pr / positives.size,
                100.0 * frReal / realNeg.size, 100.0 * frAll / negatives.size,
                100.0 * endToEnd / realNeg.size))
        }
        val endByKind = HashMap<String, Int>()
        for ((p, t, _) in realNeg) {
            val profile = TargetPoiProfile.build(p)
            val m = profile.match(t)
            if (m != null && profile.acceptsAsIdentity(m)) {
                endByKind[m.kind.name] = (endByKind[m.kind.name] ?: 0) + 1
                println("  生产路径误接受实例：目标「$p」 看到「$t」 → ${m.kind} 凭什么=${m.matchedAlias}")
            }
        }
        println("FUZZY SWEEP positives=${positives.size} negatives=${negatives.size} " +
            "(真实隔壁名负样本 ${realNeg.size})")
        print(lines)
        println("端到端身份门（生产路径）：真实隔壁名被放行 $endToEnd/${realNeg.size} " +
            "= ${"%.2f".format(100.0 * endToEnd / realNeg.size)}%  按规则=$endByKind")
        val legacy = realNeg.count { (p, t, _) -> identityAcceptedWithExplicitAliases(p, t) }
        println("对照（把派生别名当人工别名传入 = 修改前口径）：$legacy/${realNeg.size} " +
            "= ${"%.2f".format(100.0 * legacy / realNeg.size)}%")
        println("（extended 类已从 FMR 剔除：它按构造就是正样本，第一版把它当负样本是测试数据的错）")

        // 断言方向而不是具体小数
        val f75 = realNeg.count { (p, t, _) -> fuzzyBest(t, formsOf(p, aliasesOf(p))) >= 0.75 }
        val f60 = realNeg.count { (p, t, _) -> fuzzyBest(t, formsOf(p, aliasesOf(p))) >= 0.60 }
        val f90 = realNeg.count { (p, t, _) -> fuzzyBest(t, formsOf(p, aliasesOf(p))) >= 0.90 }
        assertTrue("0.60 的误接受必须不少于 0.75", f60 >= f75)
        assertTrue("0.90 的误接受必须不多于 0.75", f90 <= f75)
    }

    private fun aliasesOf(poi: String) = TargetPoiProfile.build(poi).aliases

    @Test
    fun neighbourStoresSharingPrefixAreTheRealDanger() {
        val pairs = listOf("中国工商银行" to "中国建设银行",
            "海幢街道工作委员会" to "站前街道工作委员会",
            "广州市珠海区" to "广州市荔湾区")
        val report = StringBuilder()
        for ((poi, other) in pairs) {
            val profile = TargetPoiProfile.build(poi)
            val m = profile.match(other)
            report.appendLine("$poi vs $other -> ${m?.kind} score=${m?.score} " +
                "identity=${if (m != null) profile.acceptsAsIdentity(m) else false}")
        }
        println("NEIGHBOUR PAIRS\n$report")
        for ((poi, other) in pairs) {
            val profile = TargetPoiProfile.build(poi)
            val m = profile.match(other)
            if (m != null) assertTrue("匹配结果必须带来源别名", m.matchedAlias.isNotEmpty())
        }
    }
}
