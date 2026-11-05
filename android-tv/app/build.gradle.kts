plugins { id("com.android.application") }
android {
    namespace = "com.luolihao.smarttv"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.luolihao.smarttv"
        minSdk = 23
        targetSdk = 36
        versionCode = 10
        versionName = "1.0.9"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    buildFeatures { buildConfig = true }
    lint { abortOnError = true }
    testOptions { unitTests.all { it.systemProperty("shared.fixture",rootProject.file("../schema/fixtures/channels-v1.json").absolutePath) } }
}
dependencies {
    implementation("com.google.zxing:core:3.5.3")
    implementation("androidx.media3:media3-exoplayer:1.11.1")
    implementation("androidx.media3:media3-exoplayer-hls:1.11.1")
    implementation("androidx.media3:media3-exoplayer-dash:1.11.1")
    implementation("androidx.media3:media3-ui:1.11.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
