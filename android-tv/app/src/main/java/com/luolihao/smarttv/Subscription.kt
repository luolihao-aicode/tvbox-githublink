package com.luolihao.smarttv

import java.net.URI
import org.json.JSONArray
import org.json.JSONObject

fun validateSubscriptionUrl(value:String):String {
 val url=value.trim();val uri=URI(url)
 require(url.length<=8192 && uri.scheme in listOf("http","https") && !uri.host.isNullOrBlank() && uri.userInfo==null && uri.fragment==null){"请输入有效的HTTP或HTTPS订阅地址"}
 return uri.toASCIIString()
}
object SubscriptionParser {
 fun normalize(body:String,baseUrl:String):String {
  require(body.toByteArray(Charsets.UTF_8).size<=CatalogParser.MAX_BYTES){"订阅超过5MB"}
  val text=body.trimStart('\uFEFF',' ','\n','\r','\t')
  if(text.startsWith("{")){CatalogParser.parse(text);return text}
  require(text.startsWith("#EXTM3U") && !text.contains("#EXT-X-TARGETDURATION") && !text.contains("#EXT-X-STREAM-INF")){"需要包含频道的M3U订阅，不能填写单个频道的分片清单"}
  val channels=linkedMapOf<String,JSONObject>();val used=mutableSetOf<String>()
  var name="";var group="其他";var pending=false
  for(raw in text.lineSequence()) {
   val line=raw.trim()
   when {
    line.startsWith("#EXTINF:")-> {
     val comma=line.indexOf(',',line.indexOfLast{it=='"'}+1)
     name=if(comma>=0)line.substring(comma+1).trim() else ""
     group=Regex("group-title=\"([^\"]*)\"",RegexOption.IGNORE_CASE).find(line)?.groupValues?.get(1)?.ifBlank{"其他"}?:"其他"
     pending=true
    }
    line.startsWith("#EXTGRP:")->group=line.substringAfter(':').trim().ifBlank{"其他"}
    line.isNotBlank() && !line.startsWith('#') && pending -> {
     pending=false
     val url=try{validateSubscriptionUrl(URI(baseUrl).resolve(line).toString())}catch(_:Exception){continue}
     if(name.isBlank() || name.length>256 || group.length>256 || !used.add(url))continue
     val key="$group\u0000$name"
     val channel=channels.getOrPut(key){JSONObject().put("id","m3u-"+sha256(key).take(24)).put("name",name).put("group",group).put("logo","").put("sources",JSONArray())}
     val path=URI(url).path.orEmpty().lowercase()
     val type=when {path.endsWith(".mpd")->"dash";path.endsWith(".mp4") || path.endsWith(".ts")->"progressive";else->"hls"}
     channel.getJSONArray("sources").put(JSONObject().put("id",sha256(url).take(24)).put("url",url).put("type",type).put("score",50).put("originGroup",URI(url).host))
     require(channels.size<=10000){"频道数量超过限制"}
    }
   }
  }
  require(channels.isNotEmpty()){"订阅中没有可用的HTTP(S)频道"}
  val array=JSONArray(channels.values.toList())
  return JSONObject().put("schemaVersion",1).put("minClientVersion",1).put("generatedAt","").put("channels",array).put("contentHash",CatalogParser.hashChannels(array)).toString().also{CatalogParser.parse(it)}
 }
}
