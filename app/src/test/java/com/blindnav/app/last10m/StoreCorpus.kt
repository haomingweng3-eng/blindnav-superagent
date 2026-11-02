package com.blindnav.app.last10m

/**
 * 真实店名语料的统一出处。
 *
 * 之前两个测试各自用 `Random(seed)` 拼了一遍正负样本，改一个忘改另一个，
 * 报告里就会出现"两个数对不上"。这里集中一份，所有身份/别名相关的度量都从这里取。
 *
 * 语料不是手搓的理想样例：`lsvt_names.txt` 是从 LSVT 真实中文街景招牌转写里
 * 挑出来的 198 个名字，里面天然有"中国工商银行 / 中国建设银行"这种
 * 只差两个字的隔壁店对——那正是"把隔壁认成目标"最容易发生的地方。
 */
object StoreCorpus {

    val names: List<String> by lazy {
        javaClass.classLoader!!.getResourceAsStream("last10m/lsvt_names.txt")!!
            .bufferedReader().readLines().map { it.trim() }.filter { it.isNotEmpty() }
    }

    /** 参与端到端度量的 POI 子集（与阈值扫描同一份，数字可对照）。 */
    fun pois(): List<String> = names.filter { it.length >= 4 }.take(120)

    data class Pos(val poi: String, val text: String, val kind: String)

    /** 正样本：真实店名 + 真实招牌上会发生的扰动（少字/多字/错字/全角/空格/后缀/大小写）。 */
    fun positives(): List<Pos> {
        val rnd = java.util.Random(11)
        val out = ArrayList<Pos>()
        for (poi in pois()) {
            out.add(Pos(poi, poi, "clean"))
            if (poi.length >= 3) {
                out.add(Pos(poi, poi.dropLast(1), "drop_last"))
                out.add(Pos(poi, poi.substring(1), "drop_first"))
                val i = rnd.nextInt(poi.length)
                out.add(Pos(poi, poi.replaceRange(i, i + 1, "记"), "wrong_char"))
                out.add(Pos(poi, poi + "总店", "suffix"))
                out.add(Pos(poi, poi + " " + "分店", "space_suffix"))
                out.add(Pos(poi, poi.map {
                    if (it in '0'..'9') (it.code + 0xFEE0).toChar() else it
                }.joinToString(""), "fullwidth_digit"))
            }
        }
        for (pair in listOf("STARBUCKS COFFEE" to "星巴克", "MCDONALD'S" to "麦当劳")) {
            out.add(Pos(pair.first, pair.first.lowercase(), "lowercase"))
            out.add(Pos(pair.first, "  " + pair.first + "  ", "padded"))
        }
        return out
    }

    /**
     * 负样本。三类必须分开统计：
     *  - `other_store`  真实存在的另一家店（隔壁店风险的主体）
     *  - `shared_prefix` 只共享前两个字再加别业态（"车主火锅" vs "车主服务中心"）
     *  - `extended_*`   按构造就是正样本（目标全名 + 后缀），第一版把它当负样本是测试数据的错
     */
    fun negatives(): List<Triple<String, String, String>> {
        val rnd = java.util.Random(11)
        val real = ArrayList<Triple<String, String, String>>()
        for (poi in pois()) {
            for (other in names.filter { it != poi }.shuffled(rnd).take(3)) {
                real.add(Triple(poi, other, "other_store"))
            }
            if (poi.length >= 4) {
                real.add(Triple(poi, poi.take(2) + "火锅", "shared_prefix"))
                real.add(Triple(poi, poi + "不是目标", "extended_MUST_BE_POSITIVE"))
            }
        }
        return real
    }

    /** 真实隔壁名负样本（剔除按构造就是正样本的那一类）。 */
    fun adjacentNegatives(): List<Triple<String, String, String>> =
        negatives().filter { it.third != "extended_MUST_BE_POSITIVE" }
}
