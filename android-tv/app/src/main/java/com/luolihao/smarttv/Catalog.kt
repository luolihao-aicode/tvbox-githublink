package com.luolihao.smarttv

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.URI
import java.security.MessageDigest

data class Channel(val id:String,val name:String,val group:String,val logo:String,val sources:List<Source>)
data class Catalog(val channels:List<Channel>,val generatedAt:String="",val contentHash:String="")
data class CachedCatalog(val catalog:Catalog,val body:String,val etag:String)
object CatalogParser {
 const val MAX_BYTES=5*1024*1024
 fun parse(body:String):Catalog = try { parseChecked(body) } catch(e:IllegalArgumentException) {throw e} catch(_:Exception) {throw IllegalArgumentException("JSON配置无效")}
 private fun parseChecked(body:String):Catalog {
   require(body.toByteArray(Charsets.UTF_8).size<=MAX_BYTES) {"配置超过5MB"}
   val doc=JSONObject(body)
   require(doc.getInt("schemaVersion")==1) {"不支持的配置版本"}
   require(doc.getInt("minClientVersion")<=1) {"配置需要更新的客户端；请联系配置维护者"}
   val array=doc.getJSONArray("channels")
   require(array.length()<=10000) {"频道数量超过限制"}
   val hash=doc.getString("contentHash")
   require(hash.matches(Regex("[a-fA-F0-9]{64}")) && hash.equals(hashChannels(array),true)) {"配置完整性校验失败"}
   val ids=mutableSetOf<String>();val sourceIds=mutableSetOf<String>()
   val channels=(0 until array.length()).map { i ->
     val c=array.getJSONObject(i)
     val id=c.getString("id");val name=c.getString("name");val group=c.getString("group")
     require(id.isNotBlank() && id.length<=256 && ids.add(id) && name.isNotBlank() && name.length<=256 && group.length<=256) {"频道字段或ID无效"}
     val entries=c.getJSONArray("sources");require(entries.length()>0) {"频道必须有可用来源"}
     val sources=(0 until entries.length()).map { j ->
       val s=entries.getJSONObject(j);val sid=s.getString("id");val url=s.getString("url");val uri=URI(url);val type=s.getString("type")
       require(sid.isNotBlank() && sid.length<=256 && sourceIds.add(sid)) {"来源ID重复或无效"}
       require(url.length<=8192 && uri.scheme in listOf("https","http") && !uri.host.isNullOrBlank() && uri.userInfo==null && uri.fragment==null) {"只支持无用户凭据的HTTP(S)来源"}
       require(type in listOf("hls","dash","progressive")) {"不支持的媒体协议"}
       val score=s.getDouble("score");require(score.isFinite() && score in 0.0..100.0) {"来源评分无效"}
       Source(sid,url,type,score,s.optionalString("resolution"),s.optionalString("videoCodec"),s.optionalString("lastChecked"),s.optString("originGroup",uri.host))
     }
     Channel(id,name,group,c.optString("logo",""),sources)
   }
   return Catalog(channels,doc.getString("generatedAt"),hash)
 }
 private fun JSONObject.optionalString(name:String)=if(isNull(name))null else optString(name).takeIf{it.isNotEmpty()}
 fun hashChannels(channels:JSONArray)=sha256(canonical(channels))
 fun canonical(value:Any?):String=when(value) {
   null,JSONObject.NULL -> "null"
   is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(",","{","}"){quote(it)+":"+canonical(value.get(it))}
   is JSONArray -> (0 until value.length()).joinToString(",","[","]"){canonical(value.get(it))}
   is String -> quote(value)
   is Boolean -> value.toString()
   is Number -> java.math.BigDecimal(value.toString()).stripTrailingZeros().toPlainString()
   else -> error("非法JSON值")
 }
 private fun quote(value:String):String=buildString {
   append('"');value.forEach { c -> when(c) {
     '"' -> append("\\\"");'\\' -> append("\\\\");'\b' -> append("\\b");'\u000c' -> append("\\f");'\n' -> append("\\n");'\r' -> append("\\r");'\t' -> append("\\t")
     else -> if(c.code<32)append("\\u%04x".format(c.code)) else append(c)
   } };append('"')
 }
}
class CatalogCache(val directory:File) {
 @Synchronized fun save(url:String,body:String,etag:String) {
   CatalogParser.parse(body)
   val current=currentFile(url);current.parentFile!!.mkdirs()
   val previous=File(current.parentFile,"previous.json");val temp=File(current.parentFile,"next.tmp")
   val payload=JSONObject().put("body",body).put("etag",etag.take(512)).toString().toByteArray(Charsets.UTF_8)
   FileOutputStream(temp).use{it.write(payload);it.fd.sync()}
   if(readFile(current)!=null) current.copyTo(previous,overwrite=true)
   if(!temp.renameTo(current)) {
     // Windows与部分存储不允许覆盖重命名；旧文件已有独立备份。
     if(current.exists() && !current.delete()) {temp.delete();error("无法替换配置缓存")}
     check(temp.renameTo(current)) {"无法提交配置缓存"}
   }
 }
 @Synchronized fun read(url:String):CachedCatalog?=readFile(currentFile(url)) ?: readFile(File(currentFile(url).parentFile,"previous.json"))
 private fun readFile(file:File):CachedCatalog?=try {
   if(!file.isFile || file.length()>CatalogParser.MAX_BYTES*2) null else {
     val doc=JSONObject(file.readText(Charsets.UTF_8));val body=doc.getString("body")
     CachedCatalog(CatalogParser.parse(body),body,doc.optString("etag"))
   }
 }catch(_:Exception){null}
 fun currentFile(url:String)=File(File(directory,sha256(url)),"current.json")
}
fun sha256(value:String)=MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)).joinToString(""){"%02x".format(it)}
fun validateConfigUrl(url:String):String {
 val uri=URI(url.trim())
 require(uri.scheme=="https" && !uri.host.isNullOrBlank() && uri.userInfo==null && uri.fragment==null && url.length<=8192) {"配置地址必须使用HTTPS，且不能包含用户名或密码"}
 return uri.toASCIIString()
}
