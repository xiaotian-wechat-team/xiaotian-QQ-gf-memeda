// 用 Apple Vision 给参考图打分：人脸数/最大脸面积/拍摄质量/yaw/roll + 场景分类标签（top 8, conf>=0.05）。输出 JSON Lines。
import Vision; import AppKit; import Foundation
for p in CommandLine.arguments.dropFirst() {
  var o: [String: Any] = ["path": p]
  guard let img = NSImage(contentsOfFile: p), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    o["error"] = "load"; print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!); continue }
  let r = VNDetectFaceRectanglesRequest(); let q = VNDetectFaceCaptureQualityRequest(); let c = VNClassifyImageRequest()
  try? VNImageRequestHandler(cgImage: cg).perform([r, q, c])
  let faces = r.results ?? []
  let best = faces.max { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }
  o["faces"] = faces.count; o["w"] = cg.width; o["h"] = cg.height
  o["face_area"] = Double((best?.boundingBox.width ?? 0) * (best?.boundingBox.height ?? 0))
  o["quality"] = Double((q.results ?? []).compactMap { $0.faceCaptureQuality }.max() ?? 0)
  o["yaw"] = best?.yaw?.doubleValue ?? 99; o["roll"] = best?.roll?.doubleValue ?? 99
  if let b = best?.boundingBox { o["bbox"] = [Double(b.minX), Double(1 - b.maxY), Double(b.width), Double(b.height)] }  // 左上原点，归一化 x,y,w,h
  let labs = (c.results ?? []).filter { $0.confidence >= 0.05 }.sorted { $0.confidence > $1.confidence }.prefix(8)
  o["labels"] = Dictionary(uniqueKeysWithValues: labs.map { ($0.identifier, Double(round($0.confidence * 100) / 100)) })
  print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!)
}
