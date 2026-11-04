package com.luolihao.smarttv

import android.content.Context
import org.json.JSONObject
import java.io.File

class LocalStore(context:Context) {
    companion object { const val TV_TRIAL_URL="https://luolihao-aicode.github.io/tvbox-githublink/tv-test.json" }
    private val prefs=context.getSharedPreferences("smart-tv",Context.MODE_PRIVATE)
    var configUrl:String
        get()=prefs.getString("config_url",TV_TRIAL_URL)!!.let {
            if(it=="https://luolihao-aicode.github.io/tvbox-githublink/channels.json")TV_TRIAL_URL else it
        }
        set(value){prefs.edit().putString("config_url",validateConfigUrl(value)).apply()}
    var preflight:Boolean
        get()=prefs.getBoolean("preflight",false)
        set(value){prefs.edit().putBoolean("preflight",value).apply()}
    var stableBuffer:Boolean
        get()=prefs.getBoolean("stable_buffer",true)
        set(value){prefs.edit().putBoolean("stable_buffer",value).apply()}
    fun favorites()=prefs.getStringSet("favorites",emptySet())!!.toSet()
    fun toggleFavorite(id:String) {val set=favorites().toMutableSet();if(!set.add(id))set.remove(id);prefs.edit().putStringSet("favorites",set).apply()}
    fun recent()=prefs.getString("recent","")!!.split('|').filter{it.isNotBlank()}
    fun viewed(id:String){prefs.edit().putString("recent",(listOf(id)+recent().filter{it!=id}).take(50).joinToString("|")).apply()}
    fun qualities():Map<String,Quality> = try {
        val json=JSONObject(prefs.getString("quality","{}")!!)
        json.keys().asSequence().associateWith { key -> val q=json.getJSONObject(key)
            Quality(q.optInt("successes"),q.optInt("failures"),q.optLong("watchedMs"),q.optLong("firstFrameMs"),q.optInt("stalls"),q.optInt("switches"),q.optLong("lastPlayed"))
        }
    }catch(_:Exception){emptyMap()}
    @Synchronized fun updateQuality(key:String,change:(Quality)->Quality) {
        val map=qualities().toMutableMap();map[key]=change(map[key]?:Quality())
        val json=JSONObject()
        map.entries.sortedByDescending{it.value.lastPlayed}.take(2000).forEach { (k,q) ->
            json.put(k,JSONObject().put("successes",q.successes).put("failures",q.failures).put("watchedMs",q.watchedMs).put("firstFrameMs",q.firstFrameMs).put("stalls",q.stalls).put("switches",q.switches).put("lastPlayed",q.lastPlayed))
        }
        prefs.edit().putString("quality",json.toString()).apply()
    }
    fun clearHistory(){prefs.edit().remove("quality").remove("recent").apply()}
}

class DiagnosticLog(context:Context) {
    val directory=File(context.getExternalFilesDir(android.os.Environment.DIRECTORY_DOCUMENTS)?:context.filesDir,"diagnostics").apply{mkdirs()}
    private val current=File(directory,"playback.log")
    @Synchronized fun event(name:String,details:String="") {
        if(current.length()>1024*1024) {val previous=File(directory,"playback.previous.log");previous.delete();current.renameTo(previous)}
        val line="${java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSZ",java.util.Locale.ROOT).format(java.util.Date())} $name ${redactMessage(details)}\n"
        current.appendText(line,Charsets.UTF_8)
    }
    @Synchronized fun export(snapshot:String):File {
        val file=File(directory,"diagnostics-${System.currentTimeMillis()}.txt")
        file.writeText("智选直播 本地诊断（未上传）\n${redactMessage(snapshot)}\n\n",Charsets.UTF_8)
        listOf(File(directory,"playback.previous.log"),current).filter{it.isFile}.forEach{file.appendText(it.readText(Charsets.UTF_8),Charsets.UTF_8)}
        directory.listFiles()?.filter{it.name.startsWith("diagnostics-") }?.sortedByDescending{it.lastModified()}?.drop(5)?.forEach{it.delete()}
        return file
    }
}
