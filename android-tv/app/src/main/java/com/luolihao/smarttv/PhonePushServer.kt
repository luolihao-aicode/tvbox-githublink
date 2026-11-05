package com.luolihao.smarttv

import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.security.SecureRandom

/** 仅设置页面期间运行的局域网订阅输入页；每次打开使用新的随机会话地址。 */
class PhonePushServer(private val receive:(String)->String):java.io.Closeable {
 val token=ByteArray(24).also{SecureRandom().nextBytes(it)}.joinToString(""){"%02x".format(it)}
 private val server=ServerSocket(0)
 val port:Int get()=server.localPort
 @Volatile private var closed=false
 @Volatile private var active:Socket?=null
 private val worker=kotlin.concurrent.thread(isDaemon=true,name="subscription-push") {
  while(!closed)try {server.accept().use{socket->active=socket;socket.soTimeout=5000;handle(socket);active=null}}catch(_:Exception){}
 }
 private fun handle(socket:Socket) {
  val input=socket.getInputStream()
  var total=0
  fun line():String {
   val bytes=java.io.ByteArrayOutputStream()
   while(true){val b=input.read();if(b<0)throw java.io.EOFException();require(++total<=16384);if(b==10)break;if(b!=13)bytes.write(b)}
   return bytes.toString("US-ASCII")
  }
  val request=line().split(' ');var length=0
  while(true){val header=line();if(header.isEmpty())break;if(header.startsWith("Content-Length:",true))length=header.substringAfter(':').trim().toInt()}
  fun respond(code:Int,body:String) {
   val bytes=body.toByteArray(Charsets.UTF_8)
   val reason=if(code==200)"OK" else "Error"
   socket.getOutputStream().apply{write("HTTP/1.1 $code $reason\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ${bytes.size}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n".toByteArray());write(bytes);flush()}
  }
  if(request.size<2 || request[1]!="/$token"){respond(403,"链接已失效，请重新扫描电视二维码");return}
  if(request[0]=="GET") {
   respond(200,"""<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>推送直播订阅</title><style>body{font:18px sans-serif;max-width:560px;margin:40px auto;padding:20px}input,button{box-sizing:border-box;width:100%;padding:16px;margin:10px 0;font-size:18px}</style><h2>推送直播订阅到电视</h2><p>手机和电视需连接同一路由器。支持M3U频道列表及本项目JSON目录。</p><form method="post"><input name="url" type="url" required maxlength="8192" placeholder="http://或https://订阅地址"><button>发送并加载到电视</button></form><p>请保持电视设置页面打开，加载结果会显示在这里。</p></html>""")
   return
  }
  if(request[0]!="POST" || length !in 1..16384){respond(400,"请求无效");return}
  val bytes=ByteArray(length);var read=0
  while(read<length){val count=input.read(bytes,read,length-read);if(count<0)throw java.io.EOFException();read+=count}
  try {
   val value=String(bytes,Charsets.UTF_8).split('&').firstOrNull{it.substringBefore('=')=="url"}?.substringAfter('=')?:error("缺少订阅地址")
   val result=receive(validateSubscriptionUrl(URLDecoder.decode(value,"UTF-8")))
   respond(200,"<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><h2>"+result.replace("&","&amp;").replace("<","&lt;")+"</h2><p>可返回电视选择频道。</p>")
  }catch(_:Exception){respond(400,"订阅未加载，请检查地址和网络后重试。原频道已保留。")}
 }
 override fun close(){closed=true;server.close();try{active?.close()}catch(_:Exception){} }
}
