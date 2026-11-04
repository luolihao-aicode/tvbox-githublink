package com.luolihao.smarttv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class CatalogTest {
    @Test fun sharedServerFixtureParsesWithoutHashDrift() {
        val body=java.io.File(requireNotNull(System.getProperty("shared.fixture"))).readText(Charsets.UTF_8)
        val parsed=CatalogParser.parse(body)
        assertEquals("测试频道 📺",parsed.channels.single().name)
        assertEquals("6c32f768aa9170baa13e80fbc24344a38193f5d775500239ea3d27412013fe1e",parsed.contentHash)
    }
    @Test fun authorizedChannelWithFiftyOneSourcesRemainsCompatible() {
        val doc=JSONObject(catalog());val sources=doc.getJSONArray("channels").getJSONObject(0).getJSONArray("sources")
        val original=sources.getJSONObject(0).toString()
        repeat(50){index->sources.put(JSONObject(original).put("id","extra-$index").put("url","https://example.org/$index.m3u8"))}
        doc.put("contentHash",CatalogParser.hashChannels(doc.getJSONArray("channels")))
        assertEquals(51,CatalogParser.parse(doc.toString()).channels.single().sources.size)
    }
    private fun catalog(version:Int=1):String {
        val channels=JSONArray("""[{"id":"c1","name":"测试台","group":"测试","logo":"","sources":[{"id":"s1","url":"https://example.org/live.m3u8","type":"hls","score":90,"originGroup":"example.org","availability":0.5}]}]""")
        return JSONObject().put("schemaVersion",version).put("minClientVersion",1).put("generatedAt","2026-11-04T00:00:00Z").put("contentHash",CatalogParser.hashChannels(channels)).put("channels",channels).toString()
    }
    @Test fun validDocumentAndAuthorizedEmptyCatalogAreAccepted() {
        assertEquals("测试台",CatalogParser.parse(catalog()).channels.single().name)
        val empty=JSONObject(catalog()).put("channels",JSONArray()).put("contentHash",CatalogParser.hashChannels(JSONArray())).toString()
        assertTrue(CatalogParser.parse(empty).channels.isEmpty())
    }
    @Test fun futureSchemaAndFutureClientAreRejected() {
        assertThrows(IllegalArgumentException::class.java) { CatalogParser.parse(catalog(2)) }
        assertThrows(IllegalArgumentException::class.java) { CatalogParser.parse(JSONObject(catalog()).put("minClientVersion",2).toString()) }
    }
    @Test fun corruptedHashAndUnsupportedMediaProtocolAreRejected() {
        assertThrows(IllegalArgumentException::class.java) { CatalogParser.parse(catalog().replace("测试台","被修改")) }
        val doc=JSONObject(catalog());doc.getJSONArray("channels").getJSONObject(0).getJSONArray("sources").getJSONObject(0).put("url","rtmp://example.org/live")
        doc.put("contentHash",CatalogParser.hashChannels(doc.getJSONArray("channels")))
        assertThrows(IllegalArgumentException::class.java) { CatalogParser.parse(doc.toString()) }
    }
    @Test fun canonicalHashMatchesPythonUnicodeAndNumberRules() {
        assertEquals("[{\"a\":0.5,\"n\":1,\"字\":\"中文\"}]",CatalogParser.canonical(JSONArray("""[{"字":"中文","n":1,"a":0.5}]""")))
    }
    @Test fun failedUpdateRetainsLastValidAndCorruptionFallsBackToPrevious() {
        val dir=Files.createTempDirectory("catalog-test").toFile()
        try {
            val store=CatalogCache(dir)
            store.save("https://example.org/channels.json",catalog(),"etag-one")
            store.save("https://example.org/channels.json",catalog(),"etag-two")
            assertThrows(IllegalArgumentException::class.java) { store.save("https://example.org/channels.json","broken","etag-bad") }
            assertEquals("etag-two",store.read("https://example.org/channels.json")!!.etag)
            store.currentFile("https://example.org/channels.json").writeText("corrupt")
            assertEquals("测试台",store.read("https://example.org/channels.json")!!.catalog.channels.single().name)
            assertNull(store.read("https://different.org/channels.json"))
        } finally { dir.deleteRecursively() }
    }
    @Test fun configUrlRequiresHttpsAndNoCredentials() {
        assertThrows(IllegalArgumentException::class.java) { validateConfigUrl("http://example.org/channels.json") }
        assertThrows(IllegalArgumentException::class.java) { validateConfigUrl("https://user:pass@example.org/channels.json") }
        assertEquals("https://example.org/channels.json",validateConfigUrl("https://example.org/channels.json"))
    }
}
