import { useState, useEffect } from 'react';

interface Strategy {
  id: string;
  name: string;
  description: string;
  disabled?: boolean;
}

interface StrategySelectorProps {
  value: string;
  onChange: (strategy: string) => void;
}

export function StrategySelector({ value, onChange }: StrategySelectorProps) {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // 从后端加载策略列表
    fetch('/data-process/kg/strategies')
      .then(res => res.json())
      .then(data => {
        setStrategies(data.strategies || []);
        setLoading(false);
      })
      .catch(err => {
        console.error('Failed to load strategies:', err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="strategy-selector loading">加载策略配置中...</div>;
  }

  return (
    <div className="strategy-selector">
      <h3>构建策略选择</h3>
      <div className="strategy-list">
        {strategies.map((strategy) => (
          <label
            key={strategy.id}
            className={`strategy-option ${strategy.disabled ? 'disabled' : ''} ${value === strategy.id ? 'selected' : ''}`}
          >
            <input
              type="radio"
              name="strategy"
              value={strategy.id}
              checked={value === strategy.id}
              onChange={(e) => onChange(e.target.value)}
              disabled={strategy.disabled}
            />
            <div className="strategy-content">
              <div className="strategy-name">
                {strategy.name}
                {strategy.disabled && <span className="badge-disabled">开发中</span>}
              </div>
              <div className="strategy-description">{strategy.description}</div>
            </div>
          </label>
        ))}
      </div>
    </div>
  );
}
