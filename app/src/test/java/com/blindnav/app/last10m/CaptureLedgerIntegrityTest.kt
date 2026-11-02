package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlin.random.Random

/**
 * 采集完整性 + 离线自检（任务书第 13/14 节）。
 *
 * 电脑不能调用相机，但可以真的把"写五张图 → 重新读回来"整条账本路径跑一遍：
 * 用 test resources 里的**真实 JPEG 文件**（fixture-24x18.jpg 等，由 Python 生成，
 * 是标准 SOF0 编码的 JPEG，不是占位字节）灌进 [CaptureLedger]（生产代码，同一份文件布局），
 * 再把文件读回来核对：存在、是合法 JPEG、解出的宽高与原始一致、序号与 kind 正确、
 * manifest 里组身份齐全、缺的 kind 被列出来。
 *
 * 这里不用 `javax.imageio.ImageIO`：Android 单元测试的编译类路径上 android.jar 排在前面，
 * `javax.imageio` 只有残缺桩，编译不过。改成自己读 JPEG 头（[jpegDimensions]），
 * 反而更严格——它逐段走 marker，能真的发现"写出来的是半张图"。
 *
 * 这一层为什么值得单独测：下一次拿数据线是要**采真数据**的。
 * 如果 A 店的 sign 和 B 店的门混在同一组里，那批数据整组作废——
 * 而这种错误在真机上看不出来，只有账本规则 + 自检能拦住。
 */
class CaptureLedgerIntegrityTest {

    /** 读回文件并解出宽高。返回 null 表示这不是一个结构完整的 JPEG。 */
    private fun jpegDimensions(bytes: ByteArray): Pair<Int, Int>? {
        if (bytes.size < 4) return null
        if (bytes[0].toInt() and 0xFF != 0xFF || bytes[1].toInt() and 0xFF != 0xD8) return null
        var i = 2
        while (i + 1 < bytes.size) {
            if (bytes[i].toInt() and 0xFF != 0xFF) return null      // marker 前缀丢了
            val marker = bytes[i + 1].toInt() and 0xFF
            if (marker == 0xD8 || marker == 0x01 || marker in 0xD0..0xD7) { i += 2; continue }
            if (marker == 0xD9) return null                         // 走到 EOI 都没遇到 SOF
            if (i + 3 >= bytes.size) return null
            val len = ((bytes[i + 2].toInt() and 0xFF) shl 8) or (bytes[i + 3].toInt() and 0xFF)
            if (len < 2 || i + 2 + len > bytes.size) return null
            val isSof = marker in 0xC0..0xCF && marker !in setOf(0xC4, 0xC8, 0xCC)
            if (isSof) {
                if (i + 9 >= bytes.size) return null
                val h = ((bytes[i + 5].toInt() and 0xFF) shl 8) or (bytes[i + 6].toInt() and 0xFF)
                val w = ((bytes[i + 7].toInt() and 0xFF) shl 8) or (bytes[i + 8].toInt() and 0xFF)
                return w to h
            }
            i += 2 + len
        }
        return null
    }

    private fun fixture(name: String): ByteArray =
        javaClass.classLoader!!.getResourceAsStream("last10m/$name")!!.readBytes()

    /**  fixture 的原始宽高（与生成时一致，用来验证"读回来的还是同一张图"）。 */
    private fun fixtureSize(name: String): Pair<Int, Int> = when (name) {
        "fixture-24x18.jpg" -> 24 to 18
        "fixture-40x30.jpg" -> 40 to 30
        "fixture-64x48.jpg" -> 64 to 48
        "fixture-120x90.jpg" -> 120 to 90
        else -> error("没有这个 fixture: $name")
    }

    private val FIXTURES = listOf(
        "fixture-64x48.jpg", "fixture-120x90.jpg", "fixture-40x30.jpg",
        "fixture-24x18.jpg", "fixture-64x48.jpg")

    /**
     * 其它用例只关心"账本记不记得住"，不关心像素内容，
     * 所以这里按序号从 fixture 池里挑一张真 JPEG 用（不生成假字节）。
     */
    @Suppress("UNUSED_PARAMETER")
    private fun jpeg(width: Int, height: Int, seed: Int): ByteArray =
        fixture(FIXTURES[Math.floorMod(seed, FIXTURES.size)])

    private fun tempRoot(name: String): File =
        File(System.getProperty("java.io.tmpdir"), "l10m-test-$name-${System.nanoTime()}").apply { mkdirs() }

    private class Clocks(var wall: Long = 1_700_000_000_000L, var mono: Long = 10_000L) {
        fun wallMs() = wall
        fun monoMs() = mono
        fun advance(ms: Long) { wall += ms; mono += ms }
    }

    private fun ledger(root: File, clocks: Clocks, sink: MutableList<String> = ArrayList()) =
        CaptureLedger(root, wallClockMs = { clocks.wallMs() }, monoClockMs = { clocks.monoMs() },
            events = { sink.add(it) })

    // ---------------------------------------------------------------- §14 离线自检
    @Test
    fun offlineSelfTestWritesAndReReadsFiveRealImages() {
        // 先自证 fixture 本身是合法 JPEG——否则后面"读回来能解码"这条断言是空的
        for (f in FIXTURES.distinct()) {
            val dims = jpegDimensions(fixture(f))
            assertNotNull("fixture $f 必须能被解出宽高", dims)
            assertEquals("fixture $f 宽", fixtureSize(f).first, dims!!.first)
            assertEquals("fixture $f 高", fixtureSize(f).second, dims.second)
            assertTrue("fixture 必须大于 0 且宽大于 0", dims.first > 0 && dims.second > 0)
        }
        val root = tempRoot("selftest")
        val clocks = Clocks()
        val events = ArrayList<String>()
        val l = ledger(root, clocks, events)

        val groupId = l.startGroup("星巴克咖啡", "SESSION-X")
        assertNotNull("开组必须成功", groupId)
        val written = ArrayList<CaptureLedger.CaptureRecord>()
        CaptureLedger.KINDS.forEachIndexed { i, kind ->
            clocks.advance(500)
            val bytes = fixture(FIXTURES[i])
            val rec = l.putImage(kind, bytes, frameId = 100L + i, sessionId = "SESSION-X",
                headingDegrees = 90.0 + i)
            assertNotNull("$kind 必须写成功", rec)
            written.add(rec!!)
            assertEquals("记录的字节数必须等于写进去的字节数", bytes.size.toLong(), rec.let {
                File(root, groupId!!).listFiles { f -> f.name == rec.file }!!.single().length()
            })
        }
        l.markRelation("correct_sign", "SESSION-X", targetIndex = 2)
        l.markRelation("correct_door", "SESSION-X", targetIndex = 3)
        val summary = l.finishGroup("manual")
        assertNotNull(summary)

        // —— 文件层：存在、可解码、尺寸对得上
        val dir = summary!!.directory
        val manifest = File(dir, "manifest.json")
        assertTrue("manifest.json 必须存在", manifest.isFile)
        assertTrue("index.jsonl 必须存在", File(dir, "index.jsonl").isFile)
        assertEquals("五张图都要落盘", 5, dir.listFiles { f -> f.name.endsWith(".jpg") }!!.size)
        written.forEachIndexed { i, rec ->
            val f = File(dir, rec.file)
            assertTrue("${rec.file} 必须存在", f.isFile)
            assertTrue("${rec.file} 不能是空文件", f.length() > 200)
            val bytes = f.readBytes()
            assertEquals("读回来的字节必须和写进去的一模一样", fixture(FIXTURES[i]).size, bytes.size)
            val dims = jpegDimensions(bytes)
            assertNotNull("${rec.file} 必须仍是结构完整的 JPEG", dims)
            assertEquals("${rec.file} 宽度必须原样", fixtureSize(FIXTURES[i]).first, dims!!.first)
            assertEquals("${rec.file} 高度必须原样", fixtureSize(FIXTURES[i]).second, dims.second)
            assertTrue("解码后宽高必须大于 0（实测 ${dims.first}x${dims.second}）",
                dims.first > 0 && dims.second > 0)
            assertEquals("第 ${i + 1} 张的 kind 必须是 ${CaptureLedger.KINDS[i]}",
                CaptureLedger.KINDS[i], rec.kind)
            assertEquals("序号必须连续从 1 开始", i + 1, rec.index)
            assertEquals("文件名前缀就是序号", "%04d-".format(i + 1), rec.file.substring(0, 5))
            assertEquals("每条记录都要带组身份", summary.header.groupId, rec.groupId)
            assertEquals("每条记录都要带会话身份", "SESSION-X", rec.sessionId)
            assertEquals("每条记录都要带帧号", 100L + i, rec.frameId)
            assertNotNull("每条记录都要带时间戳", rec.capturedWallMs)
            assertEquals("没有 ARCore 时必须写明只有罗盘",
                CaptureLedger.POSE_SOURCE, rec.poseSource)
        }

        // —— manifest 层：字段齐全，能被下游直接读
        val text = manifest.readText()
        for (key in listOf("groupId", "session_id", "poi_name", "schema", "started_utc",
                "start_epoch_ms", "duration_ms", "missing_kinds", "kinds_present",
                "image_count", "relation_count", "captures", "relations", "frame_id",
                "camera_heading_deg", "pose_source", "group_id")) {
            assertTrue("manifest 必须包含 $key（否则读数据的人要猜）", text.contains("\"$key\""))
        }
        assertTrue("完整一组时 missing_kinds 必须是空数组", text.contains("\"missing_kinds\": []"))
        assertTrue("POI 名要写进去", text.contains("星巴克咖啡"))
        assertTrue("关系标注要写进去", text.contains("correct_sign") && text.contains("correct_door"))
        assertEquals("五张齐全", CaptureLedger.KINDS.toSet(), summary.kindsPresent)
        assertTrue(summary.missingKinds.isEmpty())
        assertEquals("组时长用单调时钟算", 500L * 5, summary.durationMs)
        println("SELFTEST 组=${summary.header.groupId} 图=${summary.captures.size} " +
            "关系=${summary.relations.size} 时长=${summary.durationMs}ms dir=${dir.absolutePath}")
        println("  事件=${events.map { it.split(" ").first() }.distinct()}")
        assertTrue(events.any { it.startsWith("CAPTURE_GROUP_START") })
        assertTrue(events.any { it.startsWith("CAPTURE_SAVED") })
        assertTrue(events.any { it.startsWith("CAPTURE_GROUP_END") })
        root.deleteRecursively()
    }

    // ---------------------------------------------------------------- 缺失项要摊开
    @Test
    fun missingKindsAreListedInsteadOfSilentlyAbsent() {
        val root = tempRoot("missing")
        val clocks = Clocks()
        val l = ledger(root, clocks)
        l.startGroup("瑞幸咖啡", "S1")
        l.putImage("wide_scan", jpeg(320, 240, 1), 1, "S1")
        l.putImage("sign", jpeg(320, 240, 2), 2, "S1")
        val s = l.finishGroup("user_stop")!!
        assertEquals(setOf("wide_scan", "sign"), s.kindsPresent)
        assertEquals("剩下三种必须被明确列为缺失：${s.missingKinds}",
            setOf("candidate_door", "neighboring_door", "door_front_ground"), s.missingKinds)
        val text = File(s.directory, "manifest.json").readText()
        assertTrue("manifest 里要看得见缺失项",
            text.contains("candidate_door") && text.contains("door_front_ground"))
        root.deleteRecursively()
    }

    // ---------------------------------------------------------------- 不许跨会话
    @Test
    fun anotherSessionsCaptureIsRejectedNotMixed() {
        val root = tempRoot("session")
        val clocks = Clocks()
        val events = ArrayList<String>()
        val l = ledger(root, clocks, events)
        val g = l.startGroup("星巴克咖啡", "SESSION-A")!!
        val ok = l.putImage("sign", jpeg(320, 240, 7), 11, "SESSION-A")
        assertNotNull("同会话必须写成功", ok)
        // 另一段会话（换了 POI 但忘了结束组，或组被复用）想往同一组里塞图
        val bad = l.putImage("candidate_door", jpeg(320, 240, 8), 12, "SESSION-B")
        assertNull("跨会话必须拒绝", bad)
        assertTrue(events.any { it.startsWith("CAPTURE_REJECTED") && it.contains("SESSION_MISMATCH") })
        val badRelation = l.markRelation("correct_door", "SESSION-B")
        assertNull("关系标注同样不许跨会话", badRelation)
        val s = l.finishGroup()!!
        assertEquals("被拒绝的没有进账", 1, s.captures.size)
        assertEquals("组目录里只有一张图", 1, s.directory.listFiles { f -> f.name.endsWith(".jpg") }!!.size)
        // 没有组的时候写入也必须失败，不能悄悄建一个目录
        assertNull(l.putImage("wide_scan", jpeg(64, 64, 9), 13, "SESSION-A"))
        assertTrue(events.any { it.contains("NO_ACTIVE_GROUP") })
        println("  跨会话拒绝：$g 保留 ${s.captures.size} 张，事件=${events.last { it.startsWith("CAPTURE_REJECTED") }}")
        root.deleteRecursively()
    }

    // ---------------------------------------------------------------- 不许跨组
    @Test
    fun groupDataNeverLeaksIntoTheNextGroup() {
        val root = tempRoot("groups")
        val clocks = Clocks()
        val a = ledger(root, clocks)
        val nameA = a.startGroup("星巴克咖啡", "S1")!!
        a.putImage("sign", jpeg(320, 240, 21), 1, "S1")
        a.putImage("candidate_door", jpeg(320, 240, 22), 2, "S1")
        a.markRelation("correct_sign", "S1", 1)
        val sA = a.finishGroup()!!
        val b = ledger(root, clocks)
        val nameB = b.startGroup("便利超市", "S2")!!
        // 换组后序号必须重新从 1 开始，且 B 组里不能出现 A 组的任何记录
        val recB = b.putImage("sign", jpeg(320, 240, 23), 1, "S2")!!
        assertEquals("新组从第 1 张重新数起", 1, recB.index)
        assertEquals(nameB, recB.groupId)
        val sB = b.finishGroup()!!
        assertFalse("两组不能同名", nameA == nameB)
        assertEquals(1, sB.captures.size)
        assertTrue("B 组账本里不该有 A 组文件名",
            sB.captures.none { it.file.contains("candidate_door") })
        val textB = File(sB.directory, "manifest.json").readText()
        assertFalse("B 的 manifest 不许含 A 的 POI", textB.contains("星巴克咖啡"))
        assertTrue("B 的 manifest 要说清自己的会话", textB.contains("\"S2\""))
        assertEquals("A 组文件保持原样", 2, sA.captures.size)
        assertEquals("根目录里两组都在", 2, b.groupCount())
        println("  两组隔离：A=$nameA(${sA.captures.size}) B=$nameB(${sB.captures.size})")
        root.deleteRecursively()
    }

    @Test
    fun sameSecondStartGroupStillGetsUniqueGroupId() {
        // groupId 必须唯一：同一秒内连开两组不能互相覆盖，否则数据没法区分来源
        val root = tempRoot("unique")
        val fixed = Clocks(wall = 1_700_000_000_000L, mono = 0L)   // 墙钟不动
        val l1 = ledger(root, fixed)
        val n1 = l1.startGroup("店一", "S1")!!
        l1.finishGroup()
        val l2 = ledger(root, fixed)
        val n2 = l2.startGroup("店二", "S2")!!
        assertFalse("同一秒开两组也要不同 id：$n1 / $n2", n1 == n2)
        l2.putImage("sign", jpeg(160, 120, 31), 1, "S2")
        val s2 = l2.finishGroup()!!
        val t2 = File(s2.directory, "manifest.json").readText()
        assertFalse("第二组不能盖掉第一组的内容", t2.contains("店一"))
        assertEquals(2, l2.groupCount())
        root.deleteRecursively()
    }

    // ---------------------------------------------------------------- 时钟语义（§9）
    @Test
    fun wallClockRewindCannotMakeDurationNegative() {
        val root = tempRoot("clock")
        val clocks = Clocks(wall = 1_700_000_000_000L, mono = 50_000L)
        val l = ledger(root, clocks)
        l.startGroup("星巴克咖啡", "S1")
        l.putImage("sign", jpeg(200, 150, 41), 1, "S1")
        // 墙上时钟被 NTP 往回拨了一小时，单调时钟照常前进
        clocks.wall = clocks.wall - 3_600_000L
        clocks.mono += 4_000L
        val s = l.finishGroup()!!
        assertTrue("组时长不能为负（实测 ${s.durationMs}）", s.durationMs >= 0)
        assertEquals("时长只由单调时钟决定", 4_000L, s.durationMs)
        root.deleteRecursively()
    }

    @Test
    fun noGroupMeansNothingIsWritten() {
        val root = tempRoot("nogroup")
        val events = ArrayList<String>()
        val l = ledger(root, Clocks(), events)
        assertNull(l.activeGroup())
        assertNull(l.putImage("sign", jpeg(64, 64, 51), 1, "S1"))
        assertNull(l.finishGroup())
        assertEquals("一个组都没开就不该有目录", 0, l.groupCount())
        assertTrue(events.any { it.contains("NO_ACTIVE_GROUP") })
        root.deleteRecursively()
    }
}
