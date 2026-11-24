package cz.petermikula.mojevidea

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.ChannelMixingAudioProcessor
import androidx.media3.common.audio.ChannelMixingMatrix
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink

/**
 * Výstup zvuku „Stereo“: zvuk dekóduje aplikace a prostorový (5.1, 7.1) převede na dva kanály.
 *
 * Běžně přehrávač předá prostorový zvuk (Dolby, DTS) tak, jak je, televizi nebo soundbaru. Když ho
 * zařízení za televizí nezvládne nebo ho hraje jen dvěma reproduktory bez přepočtu, je slyšet hukot
 * a řeč zaniká – a přepnutí na jinou zvukovou stopu nepomůže, protože i ta bývá zakódovaná stejně.
 * V tomto režimu se proto ven posílá vždy obyčejný stereo zvuk: nic se nepředává, kanály se smíchají
 * tady (střed s řečí do obou stran, zadní tišeji, basový kanál se vynechá).
 */
@OptIn(UnstableApi::class)
class StereoRenderersFactory(context: Context) : DefaultRenderersFactory(context) {
    @Suppress("DEPRECATION")  // sestavení bez kontextu je záměr: výstup pak umí jen PCM, žádné předávání formátů
    override fun buildAudioSink(
        context: Context,
        enableFloatOutput: Boolean,
        enableAudioTrackPlaybackParams: Boolean,
    ): AudioSink = DefaultAudioSink.Builder()
        .setAudioProcessors(arrayOf<AudioProcessor>(StereoAudio.mixer()))
        .build()
}

/**
 * Výstup „Prostorový – dekóduje aplikace“: zakódovaný zvuk (Dolby, DTS) se nepředává, dekóduje ho
 * aplikace a ven jde vícekanálový zvuk PCM tak, jak je (5.1 zůstává 5.1). Pomáhá, když soundbar nebo
 * televize předaný formát nezvládne. S `bassGain` pod 1 hraje basový kanál (LFE) tišeji – pro
 * soundbary, kterým filmy s výraznými basy duní.
 */
@OptIn(UnstableApi::class)
class PcmRenderersFactory(context: Context, private val bassGain: Float) : DefaultRenderersFactory(context) {
    @Suppress("DEPRECATION")  // sestavení bez kontextu je záměr: výstup pak umí jen PCM, žádné předávání formátů
    override fun buildAudioSink(
        context: Context,
        enableFloatOutput: Boolean,
        enableAudioTrackPlaybackParams: Boolean,
    ): AudioSink {
        val builder = DefaultAudioSink.Builder()
        if (bassGain < 1f) builder.setAudioProcessors(arrayOf<AudioProcessor>(StereoAudio.bassTrim(bassGain)))
        return builder.build()
    }
}

@OptIn(UnstableApi::class)
object StereoAudio {
    const val SOFT_BASS = 0.4f          // basový kanál asi o 8 dB tišeji
    private const val LFE = 3           // pořadí kanálů z dekodérů: FL FR FC LFE BL BR …

    /**
     * Váhy, které počet kanálů nemění a jen ztiší basový kanál (u zvuku se šesti a více kanály),
     * po řádcích `[vstup * počet + výstup]`.
     */
    fun bassCoefficients(channels: Int, gain: Float): FloatArray {
        val result = FloatArray(channels * channels)
        for (index in 0 until channels) {
            result[index * channels + index] = if (channels >= 6 && index == LFE) gain else 1f
        }
        return result
    }

    /** Ztišení basového kanálu pro všechny počty kanálů, které dekodéry vydávají. */
    fun bassTrim(gain: Float): ChannelMixingAudioProcessor = ChannelMixingAudioProcessor().apply {
        for (channels in 1..8) {
            putChannelMixingMatrix(ChannelMixingMatrix(channels, channels, bassCoefficients(channels, gain)))
        }
    }

    private const val MAIN = 0.55f      // přední levý a pravý
    private const val CENTER = 0.45f    // střed (řeč) do obou stran: dohromady zní silněji než přední
    private const val SURROUND = 0.30f  // zadní a boční
    private const val BACK = 0.22f      // zadní střed (6.1) do obou stran

    /**
     * Váhy pro převod `inputs` kanálů na stereo, po řádcích `[vstup * 2 + výstup]`. Pořadí kanálů je
     * to, ve kterém je dekodéry vydávají: FL FR FC LFE BL BR (BC | SL SR).
     */
    fun coefficients(inputs: Int): FloatArray {
        // (do levého, do pravého) pro každý vstupní kanál
        val left = MAIN to 0f
        val right = 0f to MAIN
        val center = CENTER to CENTER
        val bass = 0f to 0f
        val rearLeft = SURROUND to 0f
        val rearRight = 0f to SURROUND
        val rearCenter = BACK to BACK
        val layout = when (inputs) {
            3 -> listOf(left, right, center)
            4 -> listOf(left, right, rearLeft, rearRight)
            5 -> listOf(left, right, center, rearLeft, rearRight)
            6 -> listOf(left, right, center, bass, rearLeft, rearRight)
            7 -> listOf(left, right, center, bass, rearLeft, rearRight, rearCenter)
            else -> listOf(left, right, center, bass, rearLeft, rearRight, rearLeft, rearRight)
        }
        val result = FloatArray(inputs * 2)
        for (index in 0 until inputs) {
            val (toLeft, toRight) = layout.getOrElse(index) { bass }
            result[index * 2] = toLeft
            result[index * 2 + 1] = toRight
        }
        return result
    }

    /** Směšovač pro všechny počty kanálů, které dekodéry vydávají; stereo nechává beze změny. */
    fun mixer(): ChannelMixingAudioProcessor = ChannelMixingAudioProcessor().apply {
        putChannelMixingMatrix(ChannelMixingMatrix.create(1, 2))
        putChannelMixingMatrix(ChannelMixingMatrix.create(2, 2))
        for (inputs in 3..8) putChannelMixingMatrix(ChannelMixingMatrix(inputs, 2, coefficients(inputs)))
    }
}
