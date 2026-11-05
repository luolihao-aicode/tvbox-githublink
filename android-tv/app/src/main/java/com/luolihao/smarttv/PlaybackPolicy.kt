package com.luolihao.smarttv

data class Source(val id:String,val url:String,val type:String,val score:Double,val resolution:String?=null,val videoCodec:String?=null,val lastChecked:String?=null,val originGroup:String="")
data class Quality(val successes:Int=0,val failures:Int=0,val watchedMs:Long=0,val firstFrameMs:Long=0,val stalls:Int=0,val switches:Int=0,val lastPlayed:Long=0)
data class PolicyConfig(val firstFrameTimeoutMs:Long=12000,val prepareTimeoutMs:Long=15000,val bufferTimeoutMs:Long=12000,val stallTimeoutMs:Long=12000,val cooldownMs:Long=2000,val quarantineMs:Long=60000,val stallWindowMs:Long=60000,val maxStallsInWindow:Int=3)
data class PlaybackPreferences(val preflightEnabled:Boolean=false)
enum class ActionKind { RETRY, REBUILD, REJOIN_LIVE, SWITCH, WAIT, EXHAUSTED }
data class RecoveryAction(val kind:ActionKind,val sourceId:String?=null,val delayMs:Long=0)
class RecoveryPolicy(val sources:List<Source>,val config:PolicyConfig=PolicyConfig()) {
 val quarantineUntil=mutableMapOf<String,Long>()
 var index=0; private set
 private var attempts=0
 private var lastAction:Long?=null
 private var exhausted=false
 fun failure(now:Long,fatalDecode:Boolean=false,preferBackup:Boolean=false,liveWindowExpired:Boolean=false):RecoveryAction {
   if(exhausted || sources.isEmpty()) return RecoveryAction(ActionKind.EXHAUSTED)
   val elapsed=lastAction?.let{now-it}
   if(elapsed!=null && elapsed<config.cooldownMs) return RecoveryAction(ActionKind.WAIT,delayMs=config.cooldownMs-elapsed)
   lastAction=now
   if(liveWindowExpired && attempts<2) {
     attempts++
     return RecoveryAction(ActionKind.REJOIN_LIVE,sources[index].id,500L*(1L shl attempts))
   }
   if(liveWindowExpired)attempts=2
   if(!fatalDecode && !(preferBackup && sources.size>1) && attempts<2) {
     attempts++
     return RecoveryAction(if(attempts==1) ActionKind.RETRY else ActionKind.REBUILD,sources[index].id,500L*(1L shl attempts))
   }
   quarantineUntil[sources[index].id]=now+config.quarantineMs
   val next=((index+1) until sources.size).firstOrNull{(quarantineUntil[sources[it].id]?:0)<=now}
   if(next==null) {exhausted=true;return RecoveryAction(ActionKind.EXHAUSTED)}
   index=next;attempts=0
   return RecoveryAction(ActionKind.SWITCH,sources[index].id,500)
 }
}
fun shouldPreferBackup(reason:String,hasFirstFrame:Boolean):Boolean = !hasFirstFrame && reason in setOf(
 "first_frame_timeout","prepare_timeout",
 "player_error_ERROR_CODE_IO_NETWORK_CONNECTION_FAILED","player_error_ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT"
)
class PlaybackAttemptProgress {
 var attempts=0;private set
 var failures=0;private set
 private var waitingSince:Long?=null
 private var completedWaitMs=0L
 private var sourceHasFirstFrame=false
 private var currentSourceId:String?=null
 fun begin(now:Long,sourceId:String?=null) {
   if(currentSourceId!=sourceId)sourceHasFirstFrame=false
   currentSourceId=sourceId;finish(now);attempts++;waitingSince=now
 }
 fun firstFrame(now:Long) {finish(now);sourceHasFirstFrame=true}
 fun shouldPreferBackup(reason:String)=shouldPreferBackup(reason,sourceHasFirstFrame)
 fun failed(now:Long) {finish(now);failures++}
 fun finish(now:Long) {waitingSince?.let{completedWaitMs+=(now-it).coerceAtLeast(0)};waitingSince=null}
 fun firstFrameWaitMs(now:Long):Long=completedWaitMs+(waitingSince?.let{(now-it).coerceAtLeast(0)}?:0L)
}
class NetworkAttemptProgress(private val startedAt:Long) {
 private val bytes=java.util.concurrent.atomic.AtomicLong()
 private val firstTransfer=java.util.concurrent.atomic.AtomicLong(-1)
 val transferredBytes:Long get()=bytes.get()
 val firstTransferMs:Long? get()=firstTransfer.get().takeIf{it>=0}
 fun transferStarted(now:Long) {firstTransfer.compareAndSet(-1,(now-startedAt).coerceAtLeast(0))}
 fun bytesTransferred(count:Int) {bytes.addAndGet(count.toLong())}
}
object SourceSelector {
 fun rank(sources:List<Source>,stats:Map<String,Quality>,network:String):List<Source> {
  val ranked=sources.sortedByDescending {
   val q=stats["$network:${it.id}"]
   val measured=if(q==null) 0.0 else (q.successes*35.0-q.failures*45.0-q.stalls*8.0-q.firstFrameMs/1000.0).coerceIn(-200.0,200.0)
   it.score*0.3+measured
  }
  val origins=mutableSetOf<String>()
  val preferred=mutableListOf<Source>();val aliases=mutableListOf<Source>()
  ranked.forEach { source ->
    val origin=source.originGroup.ifBlank{source.id}
    if(origins.add(origin))preferred.add(source)else aliases.add(source)
  }
  return preferred+aliases
 }
}
data class HealthSample(val now:Long,val ready:Boolean=false,val buffering:Boolean=false,val hasVideo:Boolean=true,val audioAdvancing:Boolean=false,val positionMs:Long=0,val renderedFrames:Int=0,val paused:Boolean=false)
class PlaybackHealthMonitor(val config:PolicyConfig=PolicyConfig()) {
 private var started=0L
 var firstFrameMs:Long?=null; private set
 private var bufferingAt:Long?=null
 private var lastMovement=0L
 private var lastFrameMovement=0L
 private var position=-1L
 private var frames=-1
 private val stallStarts=java.util.ArrayDeque<Long>()
 fun begin(now:Long) {started=now;firstFrameMs=null;bufferingAt=null;stallStarts.clear();lastMovement=now;lastFrameMovement=now;position=-1;frames=-1}
 fun firstFrame(now:Long) {if(firstFrameMs==null) firstFrameMs=now-started;lastFrameMovement=now}
 fun observe(s:HealthSample):String? {
   if(s.paused) {bufferingAt=null;lastMovement=s.now;lastFrameMovement=s.now;return null}
   if(s.hasVideo && firstFrameMs==null && s.now-started>=config.firstFrameTimeoutMs) return "first_frame_timeout"
   if(!s.ready && firstFrameMs==null && s.now-started>=config.prepareTimeoutMs) return "prepare_timeout"
   while(stallStarts.isNotEmpty() && s.now-stallStarts.first>config.stallWindowMs)stallStarts.removeFirst()
   if(s.buffering) {
     if(bufferingAt==null) {
       bufferingAt=s.now
       if(firstFrameMs!=null)stallStarts.addLast(s.now)
     }
     if(stallStarts.size>=config.maxStallsInWindow)return "frequent_buffering"
     if(s.now-bufferingAt!!>=config.bufferTimeoutMs)return "continuous_buffering"
   } else bufferingAt=null
   if(s.positionMs!=position) {position=s.positionMs;lastMovement=s.now}
   if(s.renderedFrames!=frames) {frames=s.renderedFrames;lastFrameMovement=s.now}
   if(s.ready && s.hasVideo && firstFrameMs!=null && !s.buffering && s.now-lastMovement>=config.stallTimeoutMs && s.now-lastFrameMovement>=config.stallTimeoutMs) return "video_progress_stalled"
   if(s.ready && s.hasVideo && firstFrameMs!=null && s.audioAdvancing && !s.buffering && s.now-lastFrameMovement>=config.stallTimeoutMs*2) return "video_frames_stalled_with_audio"
   return null
 }
}
fun redactUrl(url:String):String = try {
 val uri=java.net.URI(url)
 if(uri.scheme=="asset") "asset:///本地诊断媒体" else if(uri.host!=null) "${uri.scheme}://${uri.host}${if(uri.port>0) ":${uri.port}" else ""}/…" else "[已脱敏地址]"
} catch(_:Exception) {"[已脱敏地址]"}
fun diagnosticErrorTypes(error:Throwable)=generateSequence(error){it.cause}.take(6).joinToString(" > "){it.javaClass.simpleName}
fun redactMessage(message:String)=Regex("(?i)https?://[^\\s]+(?:\\s|$)").replace(message){redactUrl(it.value.trim())+" "}.take(2000)
