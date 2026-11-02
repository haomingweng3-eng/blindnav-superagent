package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 统一视觉结果的解析契约（任务书第 4/6 节）。
 *
 * 今晚所有能力都从这里进来，所以这一层的错误会一路传到用户耳朵里。
 * 最要紧的三条：
 *  - **结构不合格 ⇒ 整体 UNKNOWN**，不许留下半份可信字段（半份结果最危险）；
 *  - **缺失 ≠ false**：画面里"没有台阶"与"没看清有没有台阶"是两件事；
 *  - **VLM 说漏嘴的结论词只能被丢弃**，不能顺着自由文本流到 TTS。
 */
class VisionResultParserTest {

    private val frame = 7L
    private val ts = 1_000L

    private fun parse(json: String?) =
        VisionResultParser.parse(json, frame, ts, EvidenceSource.REMOTE_VLM, 1234L)

    @Test
    fun fullContractParsesEveryField() {
        val r = parse("""
{"target_visible":true,"target_confidence":950,"target_identity":"王静超市",
 "ocr_texts":[{"text":"王静超市","bbox":[10,120,830,330],"confidence":950}],
 "doors":[{"id":"door_1","bbox":[0,380,270,900],"confidence":700},
          {"id":"door_2","bbox":[350,400,620,900],"confidence":600}],
 "relations":[{"door_id":"door_1","relation":"SAME_TARGET","confidence":700},
              {"door_id":"door_2","relation":"NEIGHBOR","confidence":400}],
 "ground":{"visible":true,"step":false,"curb":null,"obstruction":false},
 "door_type":"PUSH_PULL","button_location":"LEFT_OF_DOOR",
 "need_view":"DOOR","scene_summary":"一排临街店铺，红色招牌下方是店门"}
""")!!.also { assertTrue("合法结构必须 parseOk：${it.violations}", it.parseOk) }
        assertEquals(1, r.texts.size)
        assertEquals("王静超市", r.targetIdentity)
        assertEquals(0.95, r.targetConfidence!!, 1e-9)
        assertEquals(2, r.doors.size)
        assertEquals(2, r.relations.size)
        assertEquals(RelationOutcome.LIKELY_SAME, r.relations[0].relation)
        assertEquals(RelationOutcome.CONFLICT, r.relations[1].relation)
        assertEquals(0.7, r.relations[0].confidence!!, 1e-9)
        assertEquals(false, r.ground.step)
        assertNull("curb 是 null，不能变成 false", r.ground.curb)
        assertEquals(DoorType.PUSH_PULL, r.doorType)
        assertEquals(ButtonLocation.LEFT_OF_DOOR, r.buttonLocation)
        assertEquals("REMOTE_VLM", r.source.label())
        assertEquals(1234L, r.latencyMs)
        // 千分比框必须原样保留，换算留给用帧尺寸的那一步
        assertTrue(r.doors[0].boxInFrame(1080, 1920)!!.let { it[0] == 0 && it[2] == 291 })
    }

    @Test
    fun brokenStructureYieldsWholeResultUnknown() {
        for ((name, raw) in listOf(
                "空响应" to null,
                "空串" to "   ",
                "不是 JSON" to "<html>502</html>",
                "顶层不是对象" to "[1,2,3]",
                "被 markdown 包起来" to "```json\n{\"doors\":[]}\n```")) {
            val r = parse(raw)
            assertNotNull("$name 也要有结果对象", r)
            assertFalse("$name：结构不合格就不能 parseOk", r!!.parseOk)
            assertTrue("不可信结果不能带任何门", r.doors.isEmpty() && r.texts.isEmpty())
            assertTrue("必须说清为什么不可信：${r.violations}", r.violations.isNotEmpty())
            assertEquals(name, EvidenceSource.REMOTE_VLM, r.source)
        }
        val partial = parse("{\"doors\":[{\"id\":\"x\",\"bbox\":[0,0,0,0]}]}")!!
        assertTrue("坏框只丢那一格，整体仍然可信", partial.parseOk && partial.violations.isNotEmpty())
        println("PARSER 不合格输入 5 类全部整体 UNKNOWN；坏框只丢单格：$partial.violations".take(120))
    }

    @Test
    fun badBoxesAreDroppedNotGuessed() {
        // 坐标越界 / 顺序反了 → 这个门丢掉，其余字段仍然可用；不能"顺手夹紧"成一个假框
        val r = parse("""{"doors":[{"id":"a","bbox":[0,0,1500,10]},
                                   {"id":"b","bbox":[600,400,200,900]},
                                   {"id":"c","bbox":[10,400,300,900]}],
                          "relations":[{"door_id":"c","relation":"SAME_TARGET","confidence":800}],
                          "target_visible":null}""")!!
        assertTrue(r.parseOk)
        assertEquals("两个坏框必须被丢掉", 1, r.doors.size)
        assertEquals("c", r.doors[0].id)
        assertTrue(r.violations.any { it.startsWith("door_bbox_bad") })
    }

    @Test
    fun forbiddenPhrasingInFreeTextIsDroppedNotSilencedWholeResult() {
        // 结论性说法只污染那一格：结构化字段仍然可用，但那句话绝不能播出去
        val r = parse("""{"doors":[{"id":"a","bbox":[10,400,300,900],"confidence":600}],
                          "scene_summary":"门开着，可以安全通过，往前走两米",
                          "ground":{"visible":true}}""")!!
        assertTrue("结构化部分仍然有效", r.parseOk)
        assertEquals(1, r.doors.size)
        assertNull("越权场景描述必须被丢掉", r.sceneSummary)
        assertTrue("要留下 violation 记录：${r.violations}", r.violations.any { it.startsWith("forbidden_phrase_in_scene") })
    }

    @Test
    fun unknownWordsNeverBecomeConcreteValues() {
        val r = parse("""{"door_type":"unknown","button_location":"UNKNOWN",
                          "relations":[{"door_id":"x","relation":"maybe_same","confidence":900}],
                          "target_visible":"maybe","target_confidence":12000,
                          "ground":{"visible":true,"step":"可能","curb":1,"obstruction":0}}""")!!
        assertEquals(DoorType.UNKNOWN, r.doorType)
        assertEquals(ButtonLocation.UNKNOWN, r.buttonLocation)
        assertEquals("不认识的 relation 一律 UNKNOWN", RelationOutcome.UNKNOWN, r.relations[0].relation)
        assertNull("target_visible 不是布尔就不是 false", r.targetVisible)
        assertNull("超出 0-1000 的置信度当没有", r.targetConfidence)
        assertNull("step=\"可能\" 不能猜", r.ground.step)
        assertEquals(true, r.ground.curb)
        assertEquals(false, r.ground.obstruction)
    }

    @Test
    fun provenanceLabelsEveryFieldWithItsSource() {
        val r = parse("""{"ocr_texts":[{"text":"A","bbox":[0,0,500,100]}],
                          "doors":[{"id":"d","bbox":[0,400,300,900]}],
                          "relations":[{"door_id":"d","relation":"SAME_TARGET","confidence":700}],
                          "ground":{"visible":true},"door_type":"REVOLVING",
                          "button_location":"ON_DOOR","scene_summary":"旋转门在正前方"}""")!!
        assertEquals("REMOTE_VLM", r.provenance["doors"])
        assertEquals("REMOTE_VLM_PROVISIONAL", r.provenance["relations"])
        assertEquals("REMOTE_VLM_PROVISIONAL", r.provenance["ground"])
        assertEquals("REMOTE_VLM_PROVISIONAL", r.provenance["doorType"])
        assertEquals("REMOTE_VLM_PROVISIONAL", r.provenance["button"])
        println("PARSER provenance=${r.provenance}")
    }

    @Test
    fun confidenceAcceptsBothScalesButNotNonsense() {
        val r = parse("""{"doors":[{"id":"a","bbox":[0,400,300,900],"confidence":0.78},
                                   {"id":"b","bbox":[400,400,700,900],"confidence":780},
                                   {"id":"c","bbox":[700,400,990,900],"confidence":-3}],
                          "relations":[]}""")!!
        assertEquals(0.78, r.doors[0].confidence!!, 1e-9)
        assertEquals(0.78, r.doors[1].confidence!!, 1e-9)
        assertNull("负数置信度等于没有，不能当成 0 分还留着", r.doors[2].confidence)
    }

    // ---------------------------------------------------------------- MiniJson
    @Test
    fun miniJsonHandlesRealisticPayloads() {
        val text = """{"a":[1,2,{"b":"中文\n换行 \"引号\" \\ "}],"c":true,"d":null,"e":-3.5}"""
        val o = MiniJson.parseObject(text)!!
        assertEquals(1.0, (o["a"] as List<*>)[0])
        assertEquals("中文\n换行 \"引号\" \\ ", ((o["a"] as List<*>)[2] as Map<*, *>)["b"])
        assertEquals(true, o["c"])
        assertTrue("null 必须能和\"键不存在\"区分", o.containsKey("d") && o["d"] === MiniJson.Null)
        assertEquals(-3.5, o["e"]!!)
    }

    @Test
    fun miniJsonRejectsMalformed() {
        for (bad in listOf("{", "{\"a\":}", "{\"a\" 1}", "[1,2", "{\"a\":1}}", "\"bare")) {
            val outcome = runCatching { MiniJson.parse(bad) }
            assertTrue("必须拒绝：$bad", outcome.isFailure)
        }
    }
}
