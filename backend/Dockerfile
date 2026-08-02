FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -q dependency:go-offline
COPY src ./src
RUN mvn -q -DskipTests package

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8080
# UploadNormalizer 每個上傳都會先落一份暫存檔（xlsx 轉出的 CSV 最大可達 erd.upload.max-csv-bytes
# ＝2GB），java.io.tmpdir 的預設值 /tmp 位在容器可寫層而非 PVC——大檔會撐爆 ephemeral storage。
# 因此指到 files PVC 掛載點底下；mkdir 放在啟動時做，因為 volume 會蓋掉 image 內的同名目錄。
ENV ERD_TMP_DIR=/data/files/.tmp
ENTRYPOINT ["sh", "-c", "mkdir -p \"$ERD_TMP_DIR\" && exec java -Djava.io.tmpdir=\"$ERD_TMP_DIR\" -jar app.jar"]
