package com.luolihao.smarttv

import org.junit.Assert.assertEquals
import org.junit.Test

class PagingTest {
    @Test fun refreshingEightyOneChannelsToOneReturnsToFirstPage() {
        assertEquals(1,clampChannelPage(1,81))
        assertEquals(0,clampChannelPage(1,1))
        assertEquals(0,clampChannelPage(1,0))
    }
    @Test fun oversizedPageIsClampedToLastAvailablePage() {
        assertEquals(2,clampChannelPage(99,161))
        assertEquals(0,clampChannelPage(-1,161))
    }
}
