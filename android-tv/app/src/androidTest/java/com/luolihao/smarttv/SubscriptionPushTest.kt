package com.luolihao.smarttv

import android.content.Intent
import android.graphics.drawable.BitmapDrawable
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SubscriptionPushTest {
 @Test fun decodedQrPushLoadsVideoAndPersistsWhileBadSubscriptionPreservesIt() {
  val instrumentation=InstrumentationRegistry.getInstrumentation()
  val context=instrumentation.targetContext
  val fixture=java.net.ServerSocket(0,8,java.net.InetAddress.getByName("127.0.0.1"))
  val video=context.assets.open("diagnostic.mp4").use{it.readBytes()}
  val worker=kotlin.concurrent.thread(isDaemon=true) {
   while(!fixture.isClosed)try{fixture.accept().use{socket->
    socket.soTimeout=3000
    val reader=socket.getInputStream().bufferedReader();val path=reader.readLine().split(' ')[1]
    while(!reader.readLine().isNullOrEmpty()){}
    val body=when(path){"/all.m3u"->"#EXTM3U\n#EXTINF:-1 group-title=\"测试分组\",扫码测试频道\nhttp://127.0.0.1:${fixture.localPort}/video.mp4\n".toByteArray();"/video.mp4"->video;else->"bad".toByteArray()}
    socket.getOutputStream().apply{write("HTTP/1.1 200 OK\r\nContent-Length: ${body.size}\r\nContent-Type: ${if(path.endsWith(".mp4"))"video/mp4" else "text/plain"}\r\nConnection: close\r\n\r\n".toByteArray());write(body);flush()}
   }}catch(_:Exception){}
  }
  val store=LocalStore(context);store.subscriptionUrl=""
  fun launch()=instrumentation.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
  var activity=launch()
  try {
   var qr=""
   instrumentation.runOnMainSync {
    activity.showSettings()
    fun find(view:View):ImageView? {
     if(view is ImageView && view.contentDescription=="手机推送订阅二维码")return view
     if(view is ViewGroup)for(i in 0 until view.childCount)find(view.getChildAt(i))?.let{return it}
     return null
    }
    val bitmap=(find(activity.window.decorView)!!.drawable as BitmapDrawable).bitmap
    val pixels=IntArray(bitmap.width*bitmap.height);bitmap.getPixels(pixels,0,bitmap.width,0,0,bitmap.width,bitmap.height)
    val source=com.google.zxing.RGBLuminanceSource(bitmap.width,bitmap.height,pixels)
    qr=com.google.zxing.MultiFormatReader().decode(com.google.zxing.BinaryBitmap(com.google.zxing.common.HybridBinarizer(source))).text
   }
   val uri=java.net.URI(qr);val endpoint="http://127.0.0.1:${uri.port}${uri.path}"
   assertTrue(java.net.URL(endpoint).readText().contains("推送直播订阅到电视"))
   fun post(url:String):Int {
    val connection=java.net.URL(endpoint).openConnection() as java.net.HttpURLConnection
    connection.connectTimeout=5000;connection.readTimeout=15000;connection.requestMethod="POST";connection.doOutput=true
    connection.outputStream.use{it.write(("url="+java.net.URLEncoder.encode(url,"UTF-8")).toByteArray())}
    return try{connection.responseCode}finally{connection.disconnect()}
   }
   val url="http://127.0.0.1:${fixture.localPort}/all.m3u"
   assertEquals(200,post(url));assertEquals(url,store.subscriptionUrl)
   val deadline=System.currentTimeMillis()+15000
   var rendered=false
   while(System.currentTimeMillis()<deadline && !rendered){instrumentation.runOnMainSync{rendered=activity.engine.channel?.name=="扫码测试频道" && activity.engine.firstFrameMs!=null};Thread.sleep(100)}
   assertTrue("手机提交后视频必须实际出帧",rendered)
   assertEquals(400,post("http://127.0.0.1:${fixture.localPort}/bad"))
   assertEquals(url,store.subscriptionUrl)
   instrumentation.runOnMainSync{assertEquals("扫码测试频道",activity.engine.channel?.name);activity.finish()}
   instrumentation.waitForIdleSync()
   activity=launch()
   instrumentation.runOnMainSync{assertEquals("扫码测试频道",activity.engine.channel?.name)}
  }finally{instrumentation.runOnMainSync{activity.finish()};instrumentation.waitForIdleSync();store.subscriptionUrl="";fixture.close();worker.join(1000)}
 }
}
