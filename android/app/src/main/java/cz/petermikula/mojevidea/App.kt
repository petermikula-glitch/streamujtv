package cz.petermikula.mojevidea

import android.app.Application

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Api.start(this)
    }
}
