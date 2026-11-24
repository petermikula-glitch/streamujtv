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
import androidx.media3.exoplayer.audio.ForwardingAudioSink

/**
 * Výstup zvuku přehrávače podle volby uživatele (`mode`) a posun zvuku vůči obrazu (`shiftUs`).
 *
 * - `auto`: prostorový zvuk (Dolby, DTS) se předá tak, jak je, televizi nebo soundbaru.
 * - `pcm`: zakódovaný zvuk se nepředává, dekóduje ho aplikace a ven jde vícekanálový zvuk PCM (5.1
 *   zůstává 5.1). Pomáhá, když soundbar nebo televize předaný formát nezvládne.
 * - `pcm_soft`: totéž, jen basový kanál (LFE) hraje tišeji – pro soundbary, kterým filmy duní.
 * - `stereo`: prostorový zvuk se převede na dva kanály (střed s řečí do obou stran, zadní tišeji,
 *   basový kanál se vynechá).
 *
 * Posun: soundbar zvuk zpracovává a zní pak později než obraz (nebo naopak). Přehrávač se řídí
 * časem zvuku, a tak stačí ten čas posunout – obraz i titulky se o tolik pozdrží nebo předběhnou.
 */
@OptIn(UnstableApi::class)
class AudioRenderersFactory(
    context: Context,
    private val mode: String,
    private val shiftUs: () -> Long,
) : DefaultRenderersFactory(context) {
    @Suppress("DEPRECATION")  // sestavení bez kontextu je záměr: výstup pak umí jen PCM, žádné předávání formátů
    override fun buildAudioSink(
        context: Context,
        enableFloatOutput: Boolean,
        enableAudioTrackPlaybackParams: Boolean,
    ): AudioSink? {
        val sink = when (mode) {
            "stereo" -> DefaultAudioSink.Builder().setAudioProcessors(arrayOf<AudioProcessor>(StereoAudio.mixer())).build()
            "pcm" -> DefaultAudioSink.Builder().build()
            "pcm_soft" -> DefaultAudioSink.Builder()
                .setAudioProcessors(arrayOf<AudioProcessor>(StereoAudio.bassTrim(StereoAudio.SOFT_BASS))).build()
            else -> super.buildAudioSink(context, enableFloatOutput, enableAudioTrackPlaybackParams)
        } ?: return null
        return ShiftedAudioSink(sink, shiftUs)
    }
}

/** Výstup zvuku, který hlásí čas posunutý o `shiftUs` (kladné: zvuk zní později, obraz na něj počká). */
@OptIn(UnstableApi::class)
private class ShiftedAudioSink(sink: AudioSink, private val shiftUs: () -> Long) : ForwardingAudioSink(sink) {
    override fun getCurrentPositionUs(sourceEnded: Boolean): Long {
        val position = super.getCurrentPositionUs(sourceEnded)
        return if (position == AudioSink.CURRENT_POSITION_NOT_SET) position else position - shiftUs()
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
