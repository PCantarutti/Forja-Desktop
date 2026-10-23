// Volume, mudo e saída de som do Windows (Core Audio). Compilado uma vez pelo pc.py (Add-Type -OutputAssembly) e
// chamado pelo PowerShell: [ForjaAudio]::Run(@("volume", "30")). Sem dependência: só as interfaces COM do Windows.
// IPolicyConfig (trocar o dispositivo padrão) não é documentada, mas é a que o próprio painel de som usa desde o Vista.
using System;
using System.Runtime.InteropServices;
using System.Text;

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int EnumAudioEndpoints(int flow, int mask, out IMMDeviceCollection c);
  int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice d);
}
[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection { int GetCount(out int n); int Item(int i, out IMMDevice d); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
  int OpenPropertyStore(int access, out IPropertyStore ps);
  int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
}
[Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore { int GetCount(out int n); int GetAt(int i, out PKey k); int GetValue(ref PKey k, out PropVar v); }
[StructLayout(LayoutKind.Sequential)] struct PKey { public Guid fmt; public int pid; }
[StructLayout(LayoutKind.Explicit, Size = 24)] struct PropVar { [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr p; }
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f0(); int f1(); int f2(); int f3();
  int SetMasterVolumeLevelScalar(float v, ref Guid ctx);
  int f5();
  int GetMasterVolumeLevelScalar(out float v);
  int f7(); int f8(); int f9(); int f10();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool m, ref Guid ctx);
  int GetMute([MarshalAs(UnmanagedType.Bool)] out bool m);
}
[Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig {
  int f0(); int f1(); int f2(); int f3(); int f4(); int f5(); int f6(); int f7(); int f8(); int f9();
  int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator { }
[ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] class PolicyConfigClient { }

public static class ForjaAudio {
  static IMMDeviceEnumerator En() { return (IMMDeviceEnumerator)new MMDeviceEnumerator(); }

  static IAudioEndpointVolume Vol() {
    IMMDevice d; En().GetDefaultAudioEndpoint(0, 1, out d);
    Guid iid = typeof(IAudioEndpointVolume).GUID; object o;
    d.Activate(ref iid, 23, IntPtr.Zero, out o);
    return (IAudioEndpointVolume)o;
  }

  static string Nome(IMMDevice d) {
    IPropertyStore ps; d.OpenPropertyStore(0, out ps);
    PKey k = new PKey { fmt = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };  // PKEY_Device_FriendlyName
    PropVar v; ps.GetValue(ref k, out v);
    return v.p == IntPtr.Zero ? "" : Marshal.PtrToStringUni(v.p);
  }

  static int Atual() { float v; Vol().GetMasterVolumeLevelScalar(out v); return (int)Math.Round(v * 100); }

  /// volume N | mais N | mudo 1/0 | lista | saida <id>. Devolve o volume, ou a lista "id\tnome\tpadrão" por linha.
  public static string Run(string[] a) {
    Guid ctx = Guid.Empty;
    switch (a[0]) {
      case "volume":
      case "mais":
        int alvo = a[0] == "mais" ? Atual() + int.Parse(a[1]) : int.Parse(a[1]);
        alvo = Math.Max(0, Math.Min(100, alvo));
        Vol().SetMute(false, ref ctx);
        Vol().SetMasterVolumeLevelScalar(alvo / 100f, ref ctx);
        return alvo.ToString();
      case "mudo":
        Vol().SetMute(a[1] == "1", ref ctx);
        return Atual().ToString();
      case "lista":
        IMMDeviceCollection c; En().EnumAudioEndpoints(0, 1, out c);  // saída, só os ativos
        IMMDevice padrao; En().GetDefaultAudioEndpoint(0, 1, out padrao);
        string idPadrao; padrao.GetId(out idPadrao);
        int n; c.GetCount(out n);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < n; i++) {
          IMMDevice d; c.Item(i, out d); string id; d.GetId(out id);
          sb.Append(id).Append('\t').Append(Nome(d)).Append('\t').Append(id == idPadrao ? "1" : "0").Append('\n');
        }
        return sb.ToString();
      case "saida":
        IPolicyConfig pc = (IPolicyConfig)new PolicyConfigClient();
        for (int role = 0; role < 3; role++) pc.SetDefaultEndpoint(a[1], role);  // console, multimídia e comunicação
        return "ok";
    }
    return "";
  }
}
