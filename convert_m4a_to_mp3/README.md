脚本功能                                                                                                              
                                                                                                                        
  - 自动检测当前目录中的 .m4a 文件                                                                                      
  - 使用 ffmpeg 进行高质量转换（默认 VBR 质量 2）                                                                       
  - 支持递归搜索子目录                                                                                                  
  - 自动跳过已存在的 MP3 文件                                                                                           
  - 详细的转换进度和结果报告                                                                                            
  - 错误处理和 ffmpeg 可用性检查                                                                                        
                                                                                                                        
  使用方法                                                                                                              
                                                                                                                        
  # 基本用法：转换当前目录的 M4A 文件                                                                                   
  python3 convert_m4a_to_mp3.py                                                                                         
                                                                                                                        
  # 递归转换所有子目录中的 M4A 文件                                                                                     
  python3 convert_m4a_to_mp3.py -r                                                                                      
                                                                                                                        
  # 指定输出质量（0=最佳，9=最差）                                                                                      
  python3 convert_m4a_to_mp3.py -q 0                                                                                    
                                                                                                                        
  # 指定输出目录                                                                                                        
  python3 convert_m4a_to_mp3.py -o ./mp3_output                                                                         
                                                                                                                        
  # 只显示将要转换的文件，不实际执行                                                                                    
  python3 convert_m4a_to_mp3.py --dry-run                                                                               
                                                                                                                        
  # 查看所有选项                                                                                                        
  python3 convert_m4a_to_mp3.py --help                                                                                  
                                                                                                                        
  示例输出                                                                                                              
                                                                                                                        
  Found 1 M4A file(s)                                                                                                   
  Converting: 丈八西路辅路.m4a -> 丈八西路辅路.mp3                                                                      
    ✓ Success: 0.34 MB                                                                                                  
                                                                                                                        
  ==================================================                                                                    
  Conversion Summary:                                                                                                   
    Successful: 1                                                                                                       
    Failed: 0                                                                                                           
    Total processed: 1                                                                                                  
                                                                                                                        
  注意事项                                                                                                              
                                                                                                                        
  1. 需要提前安装 ffmpeg                                                                                                
  2. 脚本会保留原始文件的元数据（如标题）
  3. 转换后的文件与原文件同名，仅扩展名改为 .mp3                                                                        
  4. 默认跳过已存在的 MP3 文件，避免重复转换                                                                            
                                                                                                                        
  脚本已测试成功，可处理包含中文和特殊字符的文件名。