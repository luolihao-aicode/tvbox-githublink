package com.luolihao.smarttv

import org.junit.Assert.*
import org.junit.Test

class ChannelMenuTest {
    private fun channel(id:String,group:String)=Channel(id,id,group,"",emptyList())
    @Test fun televisionCategoriesStartWithCctvAndSatelliteChannels() {
        assertEquals(listOf("央视","地方台","香港","台湾"),
            channelCategories(listOf(channel("tw","台湾"),channel("js","卫视"),channel("hk","香港"),channel("c1","央视"))))
    }
    @Test fun localCategoryIncludesBothSatelliteGroupSpellings() {
        val channels=listOf(channel("c1","央视"),channel("hn","卫视"),channel("js","地方频道"),channel("bj","地方台"))
        assertEquals(listOf("hn","js","bj"),menuChannels(channels,"地方台","",emptySet(),emptyList()).map{it.id})
    }
    @Test fun favoritesRecentAndSearchKeepTheirExistingMeaning() {
        val channels=listOf(channel("CCTV-1","央视"),channel("CCTV-2","央视"),channel("湖南卫视","卫视"))
        assertEquals(listOf("CCTV-2"),menuChannels(channels,"收藏","cctv",setOf("CCTV-2","湖南卫视"),emptyList()).map{it.id})
        assertEquals(listOf("湖南卫视","CCTV-1"),menuChannels(channels,"最近","",emptySet(),listOf("湖南卫视","CCTV-1")).map{it.id})
    }
    @Test fun emptyMenuStillShowsOnlyTelevisionCategories() {
        assertEquals(listOf("央视","地方台"),channelCategories(emptyList()))
    }
}
