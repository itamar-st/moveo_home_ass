import { useCallback, useEffect, useState } from "react";
import { fetchHealth, fetchHubDetail, fetchRankings } from "../api/client.js";

export function useHubs() {
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);

  const [region, setRegion] = useState("");
  const [hazard, setHazard] = useState("overall");
  const [scoreKind, setScoreKind] = useState("structural");

  const [rankings, setRankings] = useState([]);
  const [computedAt, setComputedAt] = useState(null);
  const [loadingHubs, setLoadingHubs] = useState(true);
  const [hubsError, setHubsError] = useState(null);

  const [selectedHubCode, setSelectedHubCode] = useState(null);
  const [selectedHubDetail, setSelectedHubDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const refreshHealth = useCallback(async () => {
    try {
      const data = await fetchHealth();
      setHealth(data);
      setHealthError(null);
    } catch (err) {
      setHealthError(err.message || "Backend unreachable");
    }
  }, []);

  const refreshRankings = useCallback(async () => {
    setLoadingHubs(true);
    try {
      const data = await fetchRankings({ hazard, region, scoreKind, limit: 20 });
      setRankings(data.items || []);
      setComputedAt(data.computed_at || null);
      setHubsError(null);
    } catch (err) {
      setHubsError(err.message || "Failed to load hub rankings");
    } finally {
      setLoadingHubs(false);
    }
  }, [hazard, region, scoreKind]);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  useEffect(() => {
    refreshRankings();
  }, [refreshRankings]);

  const selectHub = useCallback(async (code) => {
    if (!code) {
      setSelectedHubCode(null);
      setSelectedHubDetail(null);
      return;
    }
    setSelectedHubCode(code);
    setLoadingDetail(true);
    try {
      const detail = await fetchHubDetail(code);
      setSelectedHubDetail(detail);
    } catch {
      setSelectedHubDetail(null);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      refreshHealth(),
      refreshRankings(),
      selectedHubCode ? selectHub(selectedHubCode) : Promise.resolve(),
    ]);
  }, [refreshHealth, refreshRankings, selectedHubCode, selectHub]);

  return {
    health,
    healthError,
    region,
    setRegion,
    hazard,
    setHazard,
    scoreKind,
    setScoreKind,
    rankings,
    computedAt,
    loadingHubs,
    hubsError,
    refreshRankings,
    refreshAll,
    selectedHubCode,
    selectedHubDetail,
    loadingDetail,
    selectHub,
  };
}
