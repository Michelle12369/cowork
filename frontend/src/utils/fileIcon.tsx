import React from 'react';
import { FileTextOutlined, FileExcelOutlined } from '@ant-design/icons';

export const getFileIcon = (type: string, size = 17): React.ReactNode => {
  if (type.toLowerCase() === 'xlsx')
    return <FileExcelOutlined style={{ color: '#52c41a', fontSize: size }} />;
  return <FileTextOutlined style={{ color: '#1677ff', fontSize: size }} />;
};
