// HEAXHub Windows Worker Agent
//
// 단일 .NET 8 콘솔/서비스. 환경변수와 appsettings.json 중 환경변수가 우선.
//   HEAX_HUB_URL=https://hub.company.com
//   HEAX_AGENT_TOKEN=<운영자에게서 한 번 받은 plaintext token>
//   HEAX_AGENT_POOL=windows-cae-tools
//
// 주요 루프:
//   - heartbeat: 30초마다 POST /api/v1/agents/heartbeat
//   - poll:     5초마다 GET /api/v1/agents/poll?pool=...
//                받은 job 페이로드를 실행하고 로그/결과를 업로드
//
// 로그는 %ProgramData%\HEAXHub\agent.log 에 append 된다.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace HEAXHub.Agent;

public class AgentOptions
{
    public string HubUrl { get; set; } = "http://localhost:8000";
    public string Token { get; set; } = "";
    public string Pool { get; set; } = "default";
    public int PollIntervalSeconds { get; set; } = 5;
    public int HeartbeatIntervalSeconds { get; set; } = 30;
    public int LogFlushIntervalSeconds { get; set; } = 2;
    public string WorkRoot { get; set; } = @"C:\ProgramData\HEAXHub\work";
    public long MaxJobBytes { get; set; } = 1L * 1024L * 1024L * 1024L;
}

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        var builder = Host.CreateApplicationBuilder(args);

        // Environment variables override appsettings.json.
        builder.Configuration.AddEnvironmentVariables(prefix: "HEAX_");
        builder.Configuration.AddJsonFile("appsettings.json", optional: true);

        builder.Services.Configure<AgentOptions>(opts =>
        {
            // Hub.* + Agent.* sections
            var hub = builder.Configuration.GetSection("Hub");
            var agent = builder.Configuration.GetSection("Agent");

            opts.HubUrl = builder.Configuration["HUB_URL"]
                ?? hub["Url"] ?? opts.HubUrl;
            opts.Token = builder.Configuration["AGENT_TOKEN"]
                ?? hub["Token"] ?? opts.Token;
            opts.Pool = builder.Configuration["AGENT_POOL"]
                ?? hub["Pool"] ?? opts.Pool;

            if (int.TryParse(agent["PollIntervalSeconds"], out var pi)) opts.PollIntervalSeconds = pi;
            if (int.TryParse(agent["HeartbeatIntervalSeconds"], out var hi)) opts.HeartbeatIntervalSeconds = hi;
            if (int.TryParse(agent["LogFlushIntervalSeconds"], out var lf)) opts.LogFlushIntervalSeconds = lf;
            opts.WorkRoot = agent["WorkRoot"] ?? opts.WorkRoot;
            if (long.TryParse(agent["MaxJobBytes"], out var mb)) opts.MaxJobBytes = mb;
        });

        builder.Services.AddHttpClient("hub");
        builder.Services.AddSingleton<HubClient>();
        builder.Services.AddSingleton<JobExecutor>();
        builder.Services.AddHostedService<HeartbeatService>();
        builder.Services.AddHostedService<PollService>();
        builder.Services.AddLogging(b =>
        {
            b.AddConsole();
        });

        var host = builder.Build();
        await host.RunAsync();
        return 0;
    }
}

// ───────────────────────────── Hub HTTP client ────────────────────────────────

public class HubClient
{
    private readonly IHttpClientFactory _http;
    private readonly AgentOptions _opts;
    private readonly ILogger<HubClient> _log;

    public HubClient(IHttpClientFactory http, Microsoft.Extensions.Options.IOptions<AgentOptions> opts, ILogger<HubClient> log)
    {
        _http = http;
        _opts = opts.Value;
        _log = log;
    }

    public string AgentToken => _opts.Token;
    public string Pool => _opts.Pool;

    private HttpClient NewClient()
    {
        var c = _http.CreateClient("hub");
        c.BaseAddress = new Uri(_opts.HubUrl.TrimEnd('/') + "/");
        c.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", _opts.Token);
        c.Timeout = TimeSpan.FromSeconds(60);
        return c;
    }

    public async Task SendHeartbeatAsync(string status, string? agentVersion, CancellationToken ct)
    {
        using var c = NewClient();
        var body = new { status, agent_version = agentVersion };
        var res = await c.PostAsJsonAsync("api/v1/agents/heartbeat", body, ct);
        if (!res.IsSuccessStatusCode)
        {
            _log.LogWarning("heartbeat failed: {Status}", res.StatusCode);
        }
    }

    public async Task<JsonDocument?> PollAsync(CancellationToken ct)
    {
        using var c = NewClient();
        var res = await c.GetAsync($"api/v1/agents/poll?pool={Uri.EscapeDataString(_opts.Pool)}", ct);
        if (!res.IsSuccessStatusCode) return null;
        var json = await res.Content.ReadAsStringAsync(ct);
        return JsonDocument.Parse(json);
    }

    public async Task PostLogsAsync(string jobId, List<string> lines, CancellationToken ct)
    {
        if (lines.Count == 0) return;
        using var c = NewClient();
        var body = new { lines };
        var res = await c.PostAsJsonAsync($"api/v1/agents/jobs/{jobId}/log", body, ct);
        if (!res.IsSuccessStatusCode)
        {
            _log.LogWarning("log upload failed: {Status}", res.StatusCode);
        }
    }

    public async Task UploadResultsAsync(string jobId, string? outputZip, string? resultJsonPath, CancellationToken ct)
    {
        using var c = NewClient();
        using var form = new MultipartFormDataContent();

        if (!string.IsNullOrEmpty(outputZip) && File.Exists(outputZip))
        {
            var fs = File.OpenRead(outputZip);
            var sc = new StreamContent(fs);
            sc.Headers.ContentType = new MediaTypeHeaderValue("application/zip");
            form.Add(sc, "output_zip", "output.zip");
        }
        if (!string.IsNullOrEmpty(resultJsonPath) && File.Exists(resultJsonPath))
        {
            var rs = File.OpenRead(resultJsonPath);
            var rc = new StreamContent(rs);
            rc.Headers.ContentType = new MediaTypeHeaderValue("application/json");
            form.Add(rc, "result_json", "result.json");
        }

        var res = await c.PostAsync($"api/v1/agents/jobs/{jobId}/files", form, ct);
        if (!res.IsSuccessStatusCode)
        {
            _log.LogWarning("file upload failed: {Status}", res.StatusCode);
        }
    }

    public async Task ReportStatusAsync(string jobId, string status, int? exitCode, string? message, CancellationToken ct)
    {
        using var c = NewClient();
        var body = new { status, exit_code = exitCode, message };
        var res = await c.PostAsJsonAsync($"api/v1/agents/jobs/{jobId}/status", body, ct);
        if (!res.IsSuccessStatusCode)
        {
            _log.LogWarning("status report failed: {Status}", res.StatusCode);
        }
    }
}

// ───────────────────────────── heartbeat loop ─────────────────────────────────

public class HeartbeatService : BackgroundService
{
    private readonly HubClient _hub;
    private readonly AgentOptions _opts;
    private readonly ILogger<HeartbeatService> _log;

    public HeartbeatService(HubClient hub, Microsoft.Extensions.Options.IOptions<AgentOptions> opts, ILogger<HeartbeatService> log)
    {
        _hub = hub;
        _opts = opts.Value;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var version = typeof(HeartbeatService).Assembly.GetName().Version?.ToString() ?? "0.1.0";
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await _hub.SendHeartbeatAsync("online", version, stoppingToken);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "heartbeat failed");
            }
            await Task.Delay(TimeSpan.FromSeconds(_opts.HeartbeatIntervalSeconds), stoppingToken);
        }
    }
}

// ───────────────────────────── poll + execute loop ────────────────────────────

public class PollService : BackgroundService
{
    private readonly HubClient _hub;
    private readonly JobExecutor _executor;
    private readonly AgentOptions _opts;
    private readonly ILogger<PollService> _log;

    public PollService(HubClient hub, JobExecutor executor, Microsoft.Extensions.Options.IOptions<AgentOptions> opts, ILogger<PollService> log)
    {
        _hub = hub;
        _executor = executor;
        _opts = opts.Value;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                using var doc = await _hub.PollAsync(stoppingToken);
                if (doc != null && doc.RootElement.TryGetProperty("job", out var jobElem) && jobElem.ValueKind != JsonValueKind.Null)
                {
                    if (jobElem.TryGetProperty("control", out var ctrl))
                    {
                        _log.LogInformation("received control message: {Ctrl}", ctrl.GetRawText());
                    }
                    else
                    {
                        await _executor.RunAsync(jobElem, stoppingToken);
                    }
                }
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "poll loop iteration failed");
            }
            await Task.Delay(TimeSpan.FromSeconds(_opts.PollIntervalSeconds), stoppingToken);
        }
    }
}

// ───────────────────────────── job executor ───────────────────────────────────

public class JobExecutor
{
    private readonly HubClient _hub;
    private readonly AgentOptions _opts;
    private readonly ILogger<JobExecutor> _log;

    public JobExecutor(HubClient hub, Microsoft.Extensions.Options.IOptions<AgentOptions> opts, ILogger<JobExecutor> log)
    {
        _hub = hub;
        _opts = opts.Value;
        _log = log;
    }

    public async Task RunAsync(JsonElement payload, CancellationToken ct)
    {
        var jobId = payload.GetProperty("job_id").GetString() ?? throw new InvalidOperationException("missing job_id");
        var appId = payload.GetProperty("app_id").GetString() ?? "unknown";
        var paramsElem = payload.TryGetProperty("params", out var p) ? p : default;

        var workDir = Path.Combine(_opts.WorkRoot, jobId);
        Directory.CreateDirectory(Path.Combine(workDir, "input"));
        Directory.CreateDirectory(Path.Combine(workDir, "output"));

        var paramsPath = Path.Combine(workDir, "params.json");
        File.WriteAllText(paramsPath, paramsElem.ValueKind == JsonValueKind.Undefined ? "{}" : paramsElem.GetRawText(), Encoding.UTF8);

        _log.LogInformation("starting job {JobId} app={AppId} workDir={Work}", jobId, appId, workDir);

        // The exe path to execute should come from the app metadata or a registered shim.
        // In production this is set via system policy or a registry per app_id.
        var exePath = Environment.GetEnvironmentVariable($"HEAX_APP_EXE_{appId.ToUpperInvariant()}")
                       ?? Environment.GetEnvironmentVariable("HEAX_DEFAULT_EXE")
                       ?? "cmd.exe";
        var arguments = $"/c echo HEAXHub agent stub ran for {appId} && type \"{paramsPath}\"";
        if (!exePath.EndsWith("cmd.exe", StringComparison.OrdinalIgnoreCase))
        {
            arguments = $"\"{paramsPath}\"";
        }

        var psi = new ProcessStartInfo
        {
            FileName = exePath,
            Arguments = arguments,
            WorkingDirectory = workDir,
            UseShellExecute = false,
            RedirectStandardError = true,
            RedirectStandardOutput = true,
            CreateNoWindow = true,
        };

        var lineBuffer = new List<string>();
        var lineLock = new object();

        using var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        proc.OutputDataReceived += (_, e) => { if (e.Data != null) lock (lineLock) lineBuffer.Add(e.Data); };
        proc.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (lineLock) lineBuffer.Add(e.Data); };

        try
        {
            proc.Start();
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "failed to start process for job {JobId}", jobId);
            await _hub.ReportStatusAsync(jobId, "failed", -1, $"failed to start: {ex.Message}", ct);
            return;
        }

        var flushTask = Task.Run(async () =>
        {
            while (!proc.HasExited)
            {
                List<string> batch;
                lock (lineLock)
                {
                    batch = new List<string>(lineBuffer);
                    lineBuffer.Clear();
                }
                if (batch.Count > 0) await _hub.PostLogsAsync(jobId, batch, ct);
                await Task.Delay(TimeSpan.FromSeconds(_opts.LogFlushIntervalSeconds), ct);
            }
        }, ct);

        await proc.WaitForExitAsync(ct);

        // Flush any remaining lines.
        List<string> remaining;
        lock (lineLock)
        {
            remaining = new List<string>(lineBuffer);
            lineBuffer.Clear();
        }
        if (remaining.Count > 0) await _hub.PostLogsAsync(jobId, remaining, ct);
        try { await flushTask; } catch { /* swallow */ }

        // Zip the output directory.
        var outputDir = Path.Combine(workDir, "output");
        var outputZip = Path.Combine(workDir, "output.zip");
        if (File.Exists(outputZip)) File.Delete(outputZip);
        ZipFile.CreateFromDirectory(outputDir, outputZip);

        // Pick up result.json if the app produced one inside output/.
        var resultJsonInner = Path.Combine(outputDir, "result.json");
        var resultJson = File.Exists(resultJsonInner) ? resultJsonInner : null;
        if (resultJson == null)
        {
            // synthesize a minimal result.json
            resultJson = Path.Combine(workDir, "result.json");
            File.WriteAllText(resultJson, JsonSerializer.Serialize(new
            {
                status = proc.ExitCode == 0 ? "success" : "failed",
                summary = new { exit_code = proc.ExitCode },
                outputs = new { },
                warnings = Array.Empty<string>(),
                errors = Array.Empty<string>(),
            }));
        }

        await _hub.UploadResultsAsync(jobId, outputZip, resultJson, ct);
        await _hub.ReportStatusAsync(
            jobId,
            proc.ExitCode == 0 ? "success" : "failed",
            proc.ExitCode,
            proc.ExitCode == 0 ? null : $"exit code {proc.ExitCode}",
            ct
        );

        _log.LogInformation("finished job {JobId} exitCode={ExitCode}", jobId, proc.ExitCode);
    }
}
