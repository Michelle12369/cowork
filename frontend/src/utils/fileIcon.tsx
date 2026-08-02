import React from 'react';
import { FileTextOutlined, FileExcelOutlined } from '@ant-design/icons';

/** Picks the icon from the file NAME, not the stored type: xlsx uploads are converted to CSV at
 *  upload time, so `type` is always 'csv' and only the name still shows what the user uploaded. */
export const getFileIcon = (fileName: string, size = 17): React.ReactNode => {
  const extension = fileName.toLowerCase().split('.').pop() ?? '';
  if (extension === 'xlsx')
    return <FileExcelOutlined style={{ color: '#52c41a', fontSize: size }} />;
  return <FileTextOutlined style={{ color: '#1677ff', fontSize: size }} />;
};
