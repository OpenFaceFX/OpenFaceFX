using System;
using System.Collections.Generic;
using UnityEngine;

namespace OpenFaceFX
{
    /// <summary>One animated channel: a named curve of (time, value) keyframes.</summary>
    /// <remarks>Times are strictly ascending seconds. Values are the raw OpenFaceFX
    /// weights — 0..1 for blendshape/viseme channels, signed degrees for the
    /// head/eye rotation channels (HeadYaw/Pitch/Roll, Left/RightEyeYaw/Pitch/Roll).</remarks>
    [Serializable]
    public class OffxChannel
    {
        public string name;
        public float[] times = Array.Empty<float>();
        public float[] values = Array.Empty<float>();

        /// <summary>Linearly sample this channel at time <paramref name="t"/> (seconds),
        /// clamping to the first/last key outside the range. Matches OpenFaceFX's own
        /// linear sampling so playback reproduces the exported curve.</summary>
        public float Sample(float t)
        {
            int n = times.Length;
            if (n == 0) return 0f;
            if (t <= times[0]) return values[0];
            if (t >= times[n - 1]) return values[n - 1];
            int lo = 0, hi = n - 1;              // binary search for the bracketing segment
            while (lo + 1 < hi)
            {
                int mid = (lo + hi) >> 1;
                if (times[mid] <= t) lo = mid; else hi = mid;
            }
            float t0 = times[lo], t1 = times[hi];
            float u = (t1 > t0) ? (t - t0) / (t1 - t0) : 0f;
            return values[lo] + (values[hi] - values[lo]) * u;
        }
    }

    /// <summary>A parsed OpenFaceFX performance — the channels, frame rate and duration.
    /// Create one from a track JSON (.offxtrack) or an ARKit Live Link CSV via
    /// <see cref="OffxParser"/>, or let the editor importer build it as an asset.</summary>
    public class OffxClip : ScriptableObject
    {
        public float fps = 30f;
        public float duration;
        public List<OffxChannel> channels = new List<OffxChannel>();

        [NonSerialized] Dictionary<string, OffxChannel> _byName;

        public OffxChannel Get(string channelName)
        {
            if (_byName == null)
            {
                _byName = new Dictionary<string, OffxChannel>(StringComparer.Ordinal);
                foreach (var c in channels)
                    if (c != null && c.name != null) _byName[c.name] = c;
            }
            OffxChannel found;
            return _byName.TryGetValue(channelName, out found) ? found : null;
        }

        /// <summary>Sample a named channel at time <paramref name="t"/>; 0 if absent.</summary>
        public float Sample(string channelName, float t)
        {
            var c = Get(channelName);
            return c != null ? c.Sample(t) : 0f;
        }

        public IEnumerable<string> ChannelNames()
        {
            foreach (var c in channels)
                if (c != null && c.name != null) yield return c.name;
        }

        /// <summary>Parse an OpenFaceFX track JSON or an ARKit Live Link CSV string.</summary>
        public static OffxClip Parse(string text) => OffxParser.Parse(text);

        /// <summary>Parse a track JSON / CSV <see cref="TextAsset"/> (drop an exported
        /// file into the project and assign it directly — no importer required).</summary>
        public static OffxClip Parse(TextAsset asset) => OffxParser.Parse(asset != null ? asset.text : null);
    }
}
