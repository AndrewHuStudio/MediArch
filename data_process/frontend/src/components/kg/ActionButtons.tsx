interface ActionButtonsProps {
  isBuilding: boolean;
  onBuild: () => void;
  onClearNeo4j: () => void;
  onViewHistory: () => void;
}

export function ActionButtons({
  isBuilding,
  onBuild,
  onClearNeo4j,
  onViewHistory,
}: ActionButtonsProps) {
  return (
    <div className="action-buttons">
      <button
        className="btn-primary"
        onClick={onBuild}
        disabled={isBuilding}
      >
        {isBuilding ? '构建中...' : '开始构建'}
      </button>

      <button
        className="btn-danger"
        onClick={onClearNeo4j}
        disabled={isBuilding}
      >
        保留骨架清空
      </button>

      <button
        className="btn-secondary"
        onClick={onViewHistory}
        disabled={isBuilding}
      >
        查看历史
      </button>
    </div>
  );
}
