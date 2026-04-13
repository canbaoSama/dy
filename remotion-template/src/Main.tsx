import React from 'react';
import { AbsoluteFill, useCurrentFrame, interpolate } from 'remotion';

type Props = {
  title: string;
  source: string;
  hook: string;
};

/** 占位模板：深色 + 大字；完整方案接入脚本 JSON、字幕时间轴、素材路径 */
export const Main: React.FC<Props> = ({ title, source, hook }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: 'clamp' });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#0f0f12',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 48,
      }}
    >
      <div style={{ opacity, color: '#fef3c7', fontSize: 42, fontWeight: 700, textAlign: 'center' }}>
        {hook}
      </div>
      <div style={{ marginTop: 32, color: '#94a3b8', fontSize: 22 }}>{source}</div>
      <div style={{ marginTop: 16, color: '#e2e8f0', fontSize: 28 }}>{title}</div>
    </AbsoluteFill>
  );
};
