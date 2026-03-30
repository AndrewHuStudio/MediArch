interface BuildRecord {
  build_id: string;
  strategy: string;
  experiment_label?: string;
  timestamp: string;
  total_entities: number;
  total_relations: number;
  total_triplets: number;
  aof: number;
  build_time_seconds: number;
}

interface BuildHistoryProps {
  records: BuildRecord[];
  onDelete: (buildId: string) => void;
  onRefresh: () => void;
}

export function BuildHistory({ records, onDelete, onRefresh }: BuildHistoryProps) {
  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="build-history">
      <div className="history-header">
        <h3>构建历史</h3>
        <button className="btn-refresh" onClick={onRefresh}>
          刷新
        </button>
      </div>

      {records.length === 0 ? (
        <div className="empty-message">
          暂无构建历史
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>策略</th>
              <th>实体</th>
              <th>关系</th>
              <th>三元组</th>
              <th>AOF</th>
              <th>耗时(s)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <tr key={record.build_id}>
                <td>{formatTime(record.timestamp)}</td>
                <td>
                  <span className="strategy-badge">{record.strategy}</span>
                  {record.experiment_label && (
                    <span className="experiment-label">{record.experiment_label}</span>
                  )}
                </td>
                <td>{record.total_entities}</td>
                <td>{record.total_relations}</td>
                <td>{record.total_triplets}</td>
                <td>{record.aof.toFixed(2)}</td>
                <td>{record.build_time_seconds.toFixed(1)}</td>
                <td>
                  <button
                    className="btn-small btn-danger"
                    onClick={() => {
                      if (confirm('确定要删除这条构建记录吗?')) {
                        onDelete(record.build_id);
                      }
                    }}
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
