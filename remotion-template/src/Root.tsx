import React from 'react';
import { Composition } from 'remotion';
import { Main } from './Main';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Main"
        component={Main}
        durationInFrames={35 * 30}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          title: '海外快讯',
          source: 'Reuters',
          hook: '占位：强钩子文案',
        }}
      />
    </>
  );
};
