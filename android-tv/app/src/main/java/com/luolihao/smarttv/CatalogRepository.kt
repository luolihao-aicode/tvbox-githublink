package com.luolihao.smarttv

import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

class CatalogRepository(private val cache:CatalogCache) {
    data class Result(val catalog:Catalog?,val message:String,val cached:Boolean,val failed:Boolean=false)
    fun cached(url:String)=cache.read(url)
    fun refresh(url:String,subscription:Boolean=false):Result {
        val saved=cache.read(url)
        return try {
            fun checked(value:String)=if(subscription)validateSubscriptionUrl(value) else validateConfigUrl(value)
            var endpoint=checked(url)
            var redirects=0
            while(true) {
                val connection=(URL(endpoint).openConnection() as HttpURLConnection).apply {
                    connectTimeout=7000;readTimeout=7000;instanceFollowRedirects=false
                    setRequestProperty("Accept",if(subscription)"application/vnd.apple.mpegurl, audio/x-mpegurl, application/json, text/plain, */*" else "application/json")
                    if(endpoint==url && saved?.etag?.isNotBlank()==true) setRequestProperty("If-None-Match",saved.etag.replace("\r","").replace("\n",""))
                }
                try {
                    val code=connection.responseCode
                    if(code==304 && saved!=null) return Result(saved.catalog,"配置未变化，使用已校验缓存",true)
                    if(code in listOf(301,302,303,307,308)) {
                        require(redirects++<3){"配置重定向过多"}
                        endpoint=checked(URL(URL(endpoint),connection.getHeaderField("Location")?:error("重定向无目标")).toString());continue
                    }
                    require(code==200){"配置HTTP状态 $code"}
                    require((connection.getHeaderField("Content-Length")?.toLongOrNull()?:-1)<=CatalogParser.MAX_BYTES){"配置过大"}
                    val output=ByteArrayOutputStream()
                    connection.inputStream.use { input ->
                        val buffer=ByteArray(8192)
                        while(true) {val count=input.read(buffer);if(count<0)break;require(output.size()+count<=CatalogParser.MAX_BYTES){"配置过大"};output.write(buffer,0,count)}
                    }
                    val rawBody=output.toString("UTF-8")
                    val body=if(subscription)SubscriptionParser.normalize(rawBody,endpoint) else rawBody
                    val catalog=CatalogParser.parse(body)
                    cache.save(url,body,connection.getHeaderField("ETag")?:"")
                    return Result(catalog,if(saved?.catalog?.contentHash==catalog.contentHash) "目录内容未变化" else "已更新目录：${catalog.channels.size}个频道",false)
                } finally {connection.disconnect()}
            }
            @Suppress("UNREACHABLE_CODE") Result(saved?.catalog,"使用缓存",true)
        }catch(e:Exception) {
            Result(saved?.catalog,"更新失败：${if(e is IllegalArgumentException) e.message else e.javaClass.simpleName}。${if(saved!=null) "已回退上一份有效目录" else "尚无可用缓存，请检查网络和订阅地址"}",true,true)
        }
    }
}
