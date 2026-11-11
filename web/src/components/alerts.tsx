"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BellOff, Download, Send, Smartphone } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { addPushSubscription, alertSettings, removeAlertChannel, sendTestAlert, telegramLink } from "@/lib/api";
import { currentSubscription, deviceLabel, isIOS, isStandalone, pushSupported, subscribe, useInstallPrompt } from "@/lib/pwa";
import { Button, ErrorText, Panel } from "./ui";

const noSubscribe = () => () => {};
/** A value only the browser knows (false while rendering on the server). */
function useBrowser(read: () => boolean) {
  return useSyncExternalStore(noSubscribe, read, () => false);
}

function Note({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "good" }) {
  return (
    <p
      className={
        tone === "good" ? "rounded-xl bg-tide-soft px-3 py-2 text-sm text-[#0a6b64]" : "rounded-xl bg-harbor-soft/70 px-3 py-2 text-sm text-ink"
      }
    >
      {children}
    </p>
  );
}

function InstallSection() {
  const { canInstall, install } = useInstallPrompt();
  const standalone = useBrowser(isStandalone);
  const ios = useBrowser(isIOS);

  return (
    <div>
      <h3 className="flex items-center gap-2 font-semibold text-ink">
        <Smartphone className="size-4 text-harbor" aria-hidden /> Install the app
      </h3>
      <div className="mt-2 flex flex-col gap-2 text-sm text-slate">
        {standalone ? (
          <Note tone="good">You&apos;re using the installed CareFlow app.</Note>
        ) : canInstall ? (
          <>
            <p>Add CareFlow to this device&apos;s home screen. It opens full screen, like any other app.</p>
            <Button variant="secondary" className="w-fit" onClick={() => install()}>
              <Download className="size-4" aria-hidden /> Install CareFlow
            </Button>
          </>
        ) : ios ? (
          <p>
            On iPhone or iPad: tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>. Open CareFlow from the home screen to turn on
            alerts.
          </p>
        ) : (
          <p>
            Use the browser menu&apos;s <strong>Install app</strong> or <strong>Add to Home screen</strong>. On a computer, look for the install icon
            in the address bar.
          </p>
        )}
      </div>
    </div>
  );
}

export function AlertsPanel() {
  const qc = useQueryClient();
  const [linking, setLinking] = useState(false);
  const { data, error: loadError } = useQuery({
    queryKey: ["alert-settings"],
    queryFn: alertSettings,
    refetchOnWindowFocus: true,
    // While the person is in Telegram, notice the new connection without a reload.
    refetchInterval: (q) => (linking && !q.state.data?.channels.some((c) => c.kind === "telegram") ? 4000 : false),
  });
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [state, setState] = useState<{
    busy?: string;
    error?: unknown;
    info?: string;
  }>({});
  const supported = useBrowser(pushSupported);
  const iosBrowser = useBrowser(() => isIOS() && !isStandalone());

  useEffect(() => {
    currentSubscription()
      .then((s) => setEndpoint(s?.endpoint ?? null))
      .catch(() => setEndpoint(null));
  }, []);

  const telegramChannels = data?.channels.filter((c) => c.kind === "telegram") ?? [];
  const waitingForTelegram = linking && telegramChannels.length === 0;

  const refresh = () => qc.invalidateQueries({ queryKey: ["alert-settings"] });
  const run = async (busy: string, fn: () => Promise<string | void>) => {
    setState({ busy });
    try {
      const info = await fn();
      setState({ info: info || undefined });
    } catch (err) {
      setState({ error: err });
    } finally {
      refresh();
    }
  };

  if (loadError) return <ErrorText error={loadError} />;
  if (!data) return null;

  const thisDevice = data.channels.find((c) => c.kind === "webpush" && c.endpoint === endpoint);
  const anyChannel = data.channels.length > 0;

  const turnOn = () =>
    run("push-on", async () => {
      const sub = await subscribe(data.webPush.publicKey!);
      const json = sub.toJSON() as {
        endpoint: string;
        keys: { p256dh: string; auth: string };
      };
      await addPushSubscription({
        endpoint: json.endpoint,
        keys: json.keys,
        label: deviceLabel(),
      });
      setEndpoint(json.endpoint);
      return "Alerts are on for this device.";
    });
  const turnOff = () =>
    run("push-off", async () => {
      if (thisDevice) await removeAlertChannel(thisDevice.id);
      const sub = await currentSubscription();
      await sub?.unsubscribe();
      setEndpoint(null);
      return "Alerts are off for this device.";
    });
  const connectTelegram = () =>
    run("telegram", async () => {
      const { url, expiresInMinutes } = await telegramLink();
      window.open(url, "_blank", "noopener");
      setLinking(true);
      return `Telegram opened. Tap Start in the chat with the CareFlow bot (the link works for ${expiresInMinutes} minutes).`;
    });
  const test = () =>
    run("test", async () => {
      const r = await sendTestAlert();
      return r.sent > 0
        ? `Test alert sent to ${r.sent} of ${r.channels} place(s).`
        : "The test alert couldn't be delivered. Try turning alerts off and on again.";
    });

  return (
    <Panel className="max-w-lg p-5 sm:p-6">
      <h2 className="text-lg font-bold text-ink">Alerts on your phone</h2>
      <p className="mt-1 text-sm leading-relaxed text-slate">
        Get approvals and reminders for your role as they happen. Alerts show the ward and bed, never patient names.
      </p>

      <div className="mt-5 flex flex-col gap-6">
        <InstallSection />

        {data.demo ? (
          <Note>Alerts aren&apos;t available for demo accounts, because everyone shares them. Sign in with your own account to turn them on.</Note>
        ) : (
          <>
            <div>
              <h3 className="flex items-center gap-2 font-semibold text-ink">
                <Bell className="size-4 text-harbor" aria-hidden /> This device
              </h3>
              <div className="mt-2 flex flex-col gap-2 text-sm text-slate">
                {!data.webPush.enabled ? (
                  <p>Push alerts aren&apos;t set up on this server yet. Ask your CareFlow administrator.</p>
                ) : iosBrowser ? (
                  <p>On iPhone and iPad, alerts work only in the installed app. Add CareFlow to the home screen first (above).</p>
                ) : !supported ? (
                  <p>This browser can&apos;t receive alerts. Try Chrome, Edge, Firefox or Safari.</p>
                ) : thisDevice ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-[#0a6b64]">On for {thisDevice.label}</span>
                    <Button variant="danger" onClick={turnOff} disabled={!!state.busy}>
                      <BellOff className="size-4" aria-hidden /> Turn off
                    </Button>
                  </div>
                ) : (
                  <Button className="w-fit" onClick={turnOn} disabled={!!state.busy}>
                    <Bell className="size-4" aria-hidden /> {state.busy === "push-on" ? "Turning on" : "Turn on alerts on this device"}
                  </Button>
                )}
              </div>
            </div>

            <div>
              <h3 className="flex items-center gap-2 font-semibold text-ink">
                <Send className="size-4 text-harbor" aria-hidden /> Telegram
              </h3>
              <div className="mt-2 flex flex-col gap-2 text-sm text-slate">
                {!data.telegram.enabled ? (
                  <p>Telegram alerts aren&apos;t set up on this server yet. Ask your CareFlow administrator.</p>
                ) : (
                  <>
                    <p>Free, on any phone with Telegram. Works even when CareFlow isn&apos;t open.</p>
                    <Button variant="secondary" className="w-fit" onClick={connectTelegram} disabled={!!state.busy}>
                      <Send className="size-4" aria-hidden /> {telegramChannels.length ? "Connect another Telegram" : "Connect Telegram"}
                    </Button>
                    {waitingForTelegram && <p className="text-xs text-faint">Waiting for you to tap Start in Telegram…</p>}
                  </>
                )}
              </div>
            </div>

            {anyChannel && (
              <div>
                <h3 className="font-semibold text-ink">Where your alerts go</h3>
                <ul className="mt-2 divide-y divide-white/70 rounded-xl bg-white/50">
                  {data.channels.map((c) => (
                    <li key={c.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                      <span className="min-w-0 truncate text-ink">
                        {c.label}
                        {c.id === thisDevice?.id && <span className="text-faint"> (this device)</span>}
                      </span>
                      <Button
                        variant="danger"
                        className="px-2 py-1 text-xs"
                        disabled={!!state.busy}
                        onClick={() =>
                          run("remove", async () => {
                            await removeAlertChannel(c.id);
                            if (c.id === thisDevice?.id) {
                              await (await currentSubscription())?.unsubscribe();
                              setEndpoint(null);
                            }
                            return "Removed.";
                          })
                        }
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
                <Button variant="quiet" className="mt-2 px-2" onClick={test} disabled={!!state.busy}>
                  {state.busy === "test" ? "Sending" : "Send a test alert"}
                </Button>
              </div>
            )}
          </>
        )}

        <ErrorText error={state.error} />
        {state.info && <Note tone="good">{state.info}</Note>}
      </div>
    </Panel>
  );
}
