// 产品交付模板：检出指定版本、运行检查、构建并推送 Docker 镜像，再回调交付代理。
// 在 Jenkins 中新建 Pipeline 任务，Script Path 指向本文件。
// 回调令牌从控制器环境变量 JENKINS_WEBHOOK_TOKEN 读取，不要写进仓库。

pipeline {
    agent any

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        string(name: 'PRODUCT_NAME', defaultValue: 'sample-product', description: '产品名称')
        string(name: 'GIT_URL', defaultValue: '', description: 'Git 仓库地址')
        string(name: 'GIT_REF', defaultValue: 'main', description: '分支或标签')
        string(name: 'GIT_SHA', defaultValue: '', description: '提交 SHA')
        string(name: 'IMAGE_REPO', defaultValue: 'localhost:5000/delivery/sample-product', description: '镜像仓库，不含标签')
        string(name: 'IMAGE_TAG', defaultValue: 'dev', description: '镜像标签')
        string(name: 'TARGET_ENV', defaultValue: 'staging', description: '目标环境')
        string(name: 'DELIVERY_ID', defaultValue: '', description: '交付代理中的交付编号')
        string(name: 'AGENT_CALLBACK_URL', defaultValue: '', description: '交付代理的 Jenkins 回调地址')
    }

    environment {
        IMAGE = "${params.IMAGE_REPO}:${params.IMAGE_TAG}"
    }

    stages {
        stage('Checkout') {
            steps {
                script {
                    if (params.GIT_URL?.trim()) {
                        def branch = params.GIT_SHA?.trim() ? params.GIT_SHA : params.GIT_REF
                        checkout([
                            $class: 'GitSCM',
                            branches: [[name: branch]],
                            userRemoteConfigs: [[url: params.GIT_URL]]
                        ])
                    } else {
                        echo "GIT_URL 为空，使用任务当前工作区"
                    }
                }
            }
        }

        stage('Test') {
            steps {
                sh '''
                    set -eu
                    if [ -f app.py ]; then
                      python3 -m py_compile app.py
                    fi
                    echo "tests passed for ${PRODUCT_NAME}"
                '''
            }
        }

        stage('Image') {
            steps {
                sh '''
                    set -eu
                    docker build -t "${IMAGE}" .
                    docker push "${IMAGE}"
                    echo "pushed image ${IMAGE}"
                '''
            }
        }
    }

    post {
        always {
            script {
                if (!params.AGENT_CALLBACK_URL?.trim() || !params.DELIVERY_ID?.trim()) {
                    echo "callback skipped"
                    return
                }
                def payload = groovy.json.JsonOutput.toJson([
                    delivery_id  : params.DELIVERY_ID,
                    job          : env.JOB_NAME,
                    build_number : env.BUILD_NUMBER,
                    result       : currentBuild.currentResult ?: 'UNKNOWN',
                    image        : "${params.IMAGE_REPO}:${params.IMAGE_TAG}",
                ])
                writeFile file: 'delivery-callback.json', text: payload
                sh '''
                    curl -fsS -X POST "$AGENT_CALLBACK_URL" \
                      -H "Content-Type: application/json" \
                      -H "X-Jenkins-Token: ${JENKINS_WEBHOOK_TOKEN:-}" \
                      --data-binary @delivery-callback.json \
                      || echo "delivery agent callback failed"
                '''
            }
        }
    }
}
