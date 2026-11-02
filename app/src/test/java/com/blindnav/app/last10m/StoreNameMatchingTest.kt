package com.blindnav.app.last10m

import org.junit.Test

/**
 * 第 12 节：店名匹配覆盖 + "谁在放行错误身份"的归因。
 *
 * 上一轮扫描发现：0.75 的模糊门在真实中文店名上误接受是 0%，
 * 但端到端 `acceptsAsIdentity()` 却放行了 20.7% 的"另一个真实店名"。
 * 说明危险不在模糊阈值，而在**包含即算别名**这条规则：
 *   alias 命中用 `candidate.contains(form) || form.contains(candidate)`，
 *   而 form 又是 stripNoise 之后的短名（"和平药店"→"和平"）。
 * 于是"和平诊所"会因为包含"和平"被判成 ALIAS 0.95，直接够格确认身份。
 *
 * 这个测试不改生产行为，只把责任规则和数据摊开。
 */
class StoreNameMatchingTest {

    private val names: List<String> =
        javaClass.classLoader!!.getResourceAsStream("last10m/lsvt_names.txt")!!
            .bufferedReader().readLines().map { it.trim() }.filter { it.isNotEmpty() }

    @Test
    fun aliasCollapseExposure() {
        // 危险不在模糊阈值，在"去后缀 + 包含即算别名"：
        // 抚顺银行 → 去噪后只剩"抚顺"，于是任何写着"抚顺X"的招牌都能以 ALIAS 0.95 确认身份。
        // 这里量的是有多少 POI 会塌缩成这种短别名，也就是这条规则的暴露面。
        var collapse2 = 0
        var collapse3 = 0
        val examples = ArrayList<String>()
        for (n in names) {
            val aliases = TargetPoiProfile.build(n).aliases
            val shortest = aliases.minByOrNull { it.length } ?: continue
            if (shortest.length <= 2) {
                collapse2++
                if (examples.size < 12) examples.add("「$n」→ 最短别名「$shortest」")
            }
            if (shortest.length <= 3) collapse3++
        }
        println("ALIAS COLLAPSE exposure: 总数=${names.size} " +
            "塌缩到≤2字=$collapse2 (${ "%.1f".format(100.0 * collapse2 / names.size)}%) " +
            "≤3字=$collapse3 (${ "%.1f".format(100.0 * collapse3 / names.size)}%)")
        examples.forEach { println("  $it") }
    }

    @Test
    fun briefNamedCases() {
        val report = StringBuilder()
        data class Case(val poi: String, val text: String, val expect: String)
        val cases = listOf(
            Case("星巴克", "STARBUCKS COFFEE", "不该靠中文认出英文招牌"),
            Case("星巴克咖啡", "星巴克", "去后缀应命中"),
            Case("星巴克", "星巴克咖啡", "多后缀应命中"),
            Case("麦当劳", "McDonald's", "中英对照"),
            Case("麦当劳", "麦当劳麦乐店", "多字"),
            Case("瑞幸咖啡", "luckin coffee", "纯英文别名"),
            Case("瑞幸咖啡", "瑞幸咖", "少一个字"),
            Case("中国建设银行", "中国工商银行", "相似但不是同一目标"),
            Case("中国建设银行", "中国建设银行24小时自助银行", "包含+噪声"),
            Case("老北京火锅", "老北京涮肉", "共享前缀不同品类"),
            Case("张老三米粉店", "张老三大酒楼", "共享人名前缀"),
            Case("旺角小食店", "旺角", "只看到品牌片段"),
            Case("APPLE苹果授权专营店", "苹果授权专营店", "全半角/中英混排"),
            Case("海幢街道工作委员会", "站前街道工作委员会", "只差两个字的机构名"))
        for (c in cases) {
            val p = TargetPoiProfile.build(c.poi)
            val m = p.match(c.text)
            report.appendLine(String.format("%-22s ← %-26s %s%-8s score=%-5s identity=%s   // %s",
                c.poi, c.text, "", m?.kind ?: "NONE", m?.score ?: "-",
                if (m != null && p.acceptsAsIdentity(m)) "YES" else "no", c.expect))
        }
        println("NAMED CASES\n$report")
    }

    @Test
    fun attributeFalseAcceptanceToRules() {
        val rnd = java.util.Random(5)
        val byKind = HashMap<String, Int>()
        val examples = ArrayList<String>()
        var tested = 0
        for (poi in names.filter { it.length >= 4 }.take(120)) {
            val p = TargetPoiProfile.build(poi)
            for (other in names.filter { it != poi }.shuffled(rnd).take(5)) {
                tested++
                val m = p.match(other) ?: continue
                if (!p.acceptsAsIdentity(m)) continue
                byKind[m.kind.name] = (byKind[m.kind.name] ?: 0) + 1
                if (examples.size < 25) {
                    examples.add("目标「$poi」 看到「$other」 → ${m.kind} " +
                        "凭什么=${m.matchedAlias} score=${"%.2f".format(m.score)}")
                }
            }
        }
        println("RULE ATTRIBUTION tested=$tested")
        byKind.forEach { (k, v) -> println("  $k 放行 $v (${ "%.1f".format(100.0 * v / tested)}%)") }
        examples.forEach { println("  eg $it") }
        // 派生别名的长度分布是这件事的根因，打出来
        val aliasLens = names.take(120).flatMap { TargetPoiProfile.build(it).aliases }.map { it.length }
        if (aliasLens.isNotEmpty()) {
            println("别名长度分布：min=${aliasLens.min()} p25=${aliasLens.sorted()[aliasLens.size / 4]} " +
                "median=${aliasLens.sorted()[aliasLens.size / 2]} max=${aliasLens.max()}")
        }
    }
}
