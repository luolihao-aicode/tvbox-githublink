package com.luolihao.smarttv

import org.junit.Assert.*
import org.junit.Test

class SubscriptionTest {
 @Test fun parsesM3uGroupsRelativeUrlsAndMultipleSources() {
  val body="""#EXTM3U
#EXTINF:-1 group-title="央视",CCTV-1
https://media.example/a.m3u8
#EXTINF:-1 group-title="央视",CCTV-1
/b.m3u8
#EXTINF:-1 group-title="地方台",湖南卫视
https://media.example/hunan.m3u8
"""
  val result=CatalogParser.parse(SubscriptionParser.normalize(body,"https://lists.example/all.m3u"))
  assertEquals(2,result.channels.size)
  assertEquals(2,result.channels[0].sources.size)
  assertEquals("https://lists.example/b.m3u8",result.channels[0].sources[1].url)
  assertEquals("央视",result.channels[0].group)
 }
 @Test fun liveMediaPlaylistCannotBecomeAListOfFakeChannels() {
  try {SubscriptionParser.normalize("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\na.ts","https://example.org/a.m3u8");fail()}catch(_:IllegalArgumentException){}
 }
 @Test fun invalidPlaylistDoesNotSilentlyBecomeEmptyCatalog() {
  try {SubscriptionParser.normalize("<html>bad</html>","https://example.org/a");fail()}catch(_:IllegalArgumentException){}
 }
 @Test fun phonePushNeedsSessionTokenAndAcceptsEncodedUrl() {
  var received=""
  PhonePushServer{received=it;"已载入"}.use {server->
   val wrong=java.net.URL("http://127.0.0.1:${server.port}/wrong").openConnection() as java.net.HttpURLConnection
   assertEquals(403,wrong.responseCode);wrong.disconnect()
   val connection=java.net.URL("http://127.0.0.1:${server.port}/${server.token}").openConnection() as java.net.HttpURLConnection
   connection.requestMethod="POST";connection.doOutput=true
   connection.outputStream.use{it.write("url=https%3A%2F%2Fexample.org%2Fall.m3u%3Fx%3D1%26y%3D2".toByteArray())}
   assertEquals(200,connection.responseCode);connection.disconnect()
   assertEquals("https://example.org/all.m3u?x=1&y=2",received)
  }
 }
}
