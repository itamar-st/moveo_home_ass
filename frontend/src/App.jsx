import { useState } from "react";
import AlertToasts from "./components/AlertToasts.jsx";
import AlertsPanel from "./components/AlertsPanel.jsx";
import ChatWindow from "./components/ChatWindow.jsx";
import HubSidebar from "./components/HubSidebar.jsx";
import MethodologyModal from "./components/MethodologyModal.jsx";
import { useAlerts } from "./hooks/useAlerts.js";
import { useChatStream } from "./hooks/useChatStream.js";
import { useHubs } from "./hooks/useHubs.js";

export default function App() {
  const [methodologyOpen, setMethodologyOpen] = useState(false);

  const hubsState = useHubs();
  const chatState = useChatStream();
  const alertsState = useAlerts({ onScoringRun: hubsState.refreshAll });

  return (
    <div className="app-layout">
      <HubSidebar
        rankings={hubsState.rankings}
        region={hubsState.region}
        onRegionChange={hubsState.setRegion}
        hazard={hubsState.hazard}
        onHazardChange={hubsState.setHazard}
        scoreKind={hubsState.scoreKind}
        onScoreKindChange={hubsState.setScoreKind}
        loading={hubsState.loadingHubs}
        error={hubsState.hubsError}
        selectedHubCode={hubsState.selectedHubCode}
        selectedHubDetail={hubsState.selectedHubDetail}
        loadingDetail={hubsState.loadingDetail}
        onSelectHub={hubsState.selectHub}
        onAskAboutHub={chatState.sendMessage}
      />

      <ChatWindow
        sessionId={chatState.sessionId}
        messages={chatState.messages}
        loadingHistory={chatState.loadingHistory}
        isStreaming={chatState.isStreaming}
        statusStage={chatState.statusStage}
        liveTrace={chatState.liveTrace}
        errorState={chatState.errorState}
        onClearError={chatState.clearError}
        sendBurstKey={chatState.sendBurstKey}
        availableModels={chatState.availableModels}
        selectedModel={chatState.selectedModel}
        onSelectModel={chatState.setSelectedModel}
        onSendMessage={chatState.sendMessage}
        onNewChat={chatState.newChat}
        onOpenMethodology={() => setMethodologyOpen(true)}
      />

      <AlertsPanel
        health={hubsState.health}
        alerts={alertsState.alerts}
        loading={alertsState.loading}
        streamConnected={alertsState.streamConnected}
        onAskAlerts={chatState.sendMessage}
      />

      <AlertToasts
        toasts={alertsState.toasts}
        onDismiss={alertsState.dismissToast}
      />

      <MethodologyModal
        isOpen={methodologyOpen}
        onClose={() => setMethodologyOpen(false)}
      />
    </div>
  );
}
