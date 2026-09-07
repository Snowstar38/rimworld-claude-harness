using System;
using RimBridgeServer.Sdk;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// Phase 1 smoke tool. Its only job is to prove the whole chain works:
    /// the DLL was found in a BridgeTools folder, loaded, scanned for [Tool]
    /// methods, instantiated (public, parameterless), and registered onto the
    /// GAB tool surface. It deliberately touches nothing in the game.
    /// </summary>
    public sealed class HomePingTools
    {
        [Tool(
            "home/ping",
            Title = "Home companion ping",
            Description = "Liveness check for the C:\\Home RimBridgeServer companion DLL. Touches no game state.",
            ResultDescription = "success, a fixed pong label, the companion assembly version, and whether the call ran on RimWorld's main thread.")]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public object Ping(
            IRimBridgeContext ctx,
            [ToolParameter(Description = "Optional label echoed back so a caller can correlate the response")] string label = null)
        {
            return BridgeCommon.WithUnknownArguments(
                PingCore(ctx, label), ctx, typeof(HomePingTools), "home/ping");
        }

        private static object PingCore(IRimBridgeContext ctx, string label)
        {
            return new
            {
                success = true,
                pong = label ?? "pong",
                companion = "HomeBridge.BridgeTools",
                companionVersion = typeof(HomePingTools).Assembly.GetName().Version?.ToString() ?? "unknown",
                sdkVersion = typeof(ToolAttribute).Assembly.GetName().Version?.ToString() ?? "unknown",
                // Companion tools are dispatched with MarshalToMainThread = false,
                // so this is expected to be false. Reported so the load test can
                // confirm that rather than assume it.
                onMainThread = SafeIsMainThread(ctx),
                utc = DateTime.UtcNow.ToString("o")
            };
        }

        private static bool? SafeIsMainThread(IRimBridgeContext ctx)
        {
            try
            {
                return ctx?.MainThread?.IsMainThread;
            }
            catch
            {
                return null;
            }
        }
    }
}
