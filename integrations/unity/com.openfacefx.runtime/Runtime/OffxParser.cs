using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

namespace OpenFaceFX
{
    /// <summary>Reads the two OpenFaceFX interchange formats into an <see cref="OffxClip"/>:
    /// the self-describing track JSON (<c>openfacefx.track</c>, recommended) and the
    /// Apple ARKit "Live Link Face" wide CSV that OpenFaceFX's ARKit exporter writes.</summary>
    public static class OffxParser
    {
        /// <summary>Default frame rate for a Live Link CSV. OpenFaceFX's exporter writes
        /// the SMPTE timecode at 60 fps unless told otherwise; the CSV rows are a uniform
        /// grid, so time is <c>rowIndex / fps</c>. Override on the importer/player if you
        /// exported at a different rate. The track JSON carries explicit times and ignores this.</summary>
        public const float DefaultCsvFps = 60f;

        public static OffxClip Parse(string text) => Parse(text, DefaultCsvFps);

        public static OffxClip Parse(string text, float csvFps)
        {
            if (string.IsNullOrEmpty(text))
                throw new ArgumentException("empty OpenFaceFX data");
            string head = text.TrimStart();
            if (head.Length > 0 && head[0] == '{')
                return FromTrackJson(text);
            if (head.StartsWith("Timecode", StringComparison.OrdinalIgnoreCase) ||
                head.IndexOf("BlendShapeCount", StringComparison.OrdinalIgnoreCase) >= 0)
                return FromArkitCsv(text, csvFps);
            throw new FormatException(
                "unrecognised OpenFaceFX data — expected an openfacefx.track JSON object or an ARKit Live Link CSV");
        }

        // ---- track JSON: { format, version, fps, duration, channels:[{name, keys:[[t,v]...]}] } ----
        public static OffxClip FromTrackJson(string json)
        {
            var root = MiniJson.Parse(json) as Dictionary<string, object>;
            if (root == null) throw new FormatException("track JSON root is not an object");

            var clip = ScriptableObject.CreateInstance<OffxClip>();
            clip.fps = ToF(Get(root, "fps"), 30f);
            clip.duration = ToF(Get(root, "duration"), 0f);

            var chans = Get(root, "channels") as List<object>;
            if (chans != null)
            {
                foreach (var co in chans)
                {
                    var cd = co as Dictionary<string, object>;
                    if (cd == null) continue;
                    var ch = new OffxChannel { name = Get(cd, "name") as string };
                    var keys = Get(cd, "keys") as List<object>;
                    int m = keys != null ? keys.Count : 0;
                    ch.times = new float[m];
                    ch.values = new float[m];
                    for (int i = 0; i < m; i++)
                    {
                        var kv = keys[i] as List<object>;
                        if (kv != null && kv.Count >= 2)
                        {
                            ch.times[i] = ToF(kv[0], 0f);
                            ch.values[i] = ToF(kv[1], 0f);
                        }
                    }
                    clip.channels.Add(ch);
                }
            }
            if (clip.duration <= 0f) clip.duration = MaxLastTime(clip);
            return clip;
        }

        // ---- ARKit Live Link wide CSV: Timecode,BlendShapeCount,<61 named columns> ----
        public static OffxClip FromArkitCsv(string csv, float fps)
        {
            if (fps <= 0f) fps = DefaultCsvFps;
            var lines = csv.Replace("\r\n", "\n").Replace("\r", "\n").Split('\n');
            if (lines.Length < 2) throw new FormatException("ARKit CSV has no data rows");

            var header = lines[0].Split(',');
            const int firstValueCol = 2;              // 0=Timecode, 1=BlendShapeCount
            var names = new List<string>();
            for (int c = firstValueCol; c < header.Length; c++) names.Add(header[c].Trim());

            int nrows = 0;
            for (int r = 1; r < lines.Length; r++) if (lines[r].Trim().Length > 0) nrows++;

            var times = new float[nrows];
            var cols = new float[names.Count][];
            for (int c = 0; c < names.Count; c++) cols[c] = new float[nrows];

            int ri = 0;
            for (int r = 1; r < lines.Length; r++)
            {
                string line = lines[r];
                if (line.Trim().Length == 0) continue;
                var f = line.Split(',');
                times[ri] = ri / fps;                  // uniform grid — exactly how OpenFaceFX writes rows
                for (int c = 0; c < names.Count; c++)
                {
                    int fi = firstValueCol + c;
                    cols[c][ri] = fi < f.Length ? ToF(f[fi], 0f) : 0f;
                }
                ri++;
            }

            var clip = ScriptableObject.CreateInstance<OffxClip>();
            clip.fps = fps;
            clip.duration = nrows > 0 ? (nrows - 1) / fps : 0f;
            for (int c = 0; c < names.Count; c++)
                clip.channels.Add(new OffxChannel { name = names[c], times = times, values = cols[c] });
            return clip;
        }

        static float MaxLastTime(OffxClip clip)
        {
            float mx = 0f;
            foreach (var c in clip.channels)
                if (c != null && c.times.Length > 0) mx = Mathf.Max(mx, c.times[c.times.Length - 1]);
            return mx;
        }

        static object Get(Dictionary<string, object> d, string k)
        {
            object v;
            return d.TryGetValue(k, out v) ? v : null;
        }

        static float ToF(object o, float def)
        {
            if (o == null) return def;
            if (o is double) return (float)(double)o;
            if (o is float) return (float)o;
            if (o is int) return (int)o;
            if (o is long) return (long)o;
            float v;
            return float.TryParse(o.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : def;
        }
    }
}
